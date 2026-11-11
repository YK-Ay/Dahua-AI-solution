"""规则版「需求 → Spec」构建器（无 LLM Key 时的 P0 实现）。

流程：解析需求文本 → 行业路由 → 场景识别与路数分配 → 选型（只从产品白名单里挑）
→ 确定性计算存储/带宽 → 硬闸门校验。
LLM 接入后只替换「解析需求文本」这一步，后续计算与选型保持不变。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .calc import calc_bandwidth, calc_storage, total_lanes
from .kb import ProductRepo
from .models import (
    Boundaries,
    Device,
    Network,
    Project,
    Requirement,
    Scale,
    Spec,
    Storage,
)
from .platform import build_platform
from .selection import select_camera
from .taxonomy import (
    FALLBACK_SCENE,
    GENERIC_CAMERA_CLASSES,
    INDUSTRIES as _INDUSTRIES,
    all_scenes,
    industry_of,
    scene as scene_def,
)

# 场景表数据在 app/taxonomy.py；这里保留 SCENE_RULES 这个名字给解析层与测试用
SCENE_RULES = all_scenes()

DEFAULT_LANES = 50
DEFAULT_RETENTION_DAYS = 30
NVR_CHANNELS = 32
DISK_CAPACITY_TB = 8
SWITCH_PORTS = 24


class SpecGateError(Exception):
    """硬闸门：型号不在白名单或关键信息缺失时终止，不输出半成品方案。"""

    def __init__(self, gaps: list[str]):
        self.gaps = gaps
        super().__init__("；".join(gaps))


# 方案库行业口径：53 个配单只覆盖 20 个行业，行业统称需要映射后才命中
BOM_INDUSTRY_MAP = {name: spec["bom_industries"] for name, spec in _INDUSTRIES.items()}

# 显示层分级：(总路数上限, 列, 行)
DISPLAY_TIERS = [(16, 1, 1), (48, 2, 2), (128, 3, 3), (10 ** 9, 4, 3)]


def bom_industries(industry: str, tags: list[str]) -> list[str]:
    names = [t for t in tags if t]
    names += BOM_INDUSTRY_MAP.get(industry, [industry])
    return list(dict.fromkeys(names))


def display_plan(lanes: int) -> tuple[int, int]:
    """按总点位确定电视墙列×行；16 路以内单屏。"""
    for cap, cols, rows in DISPLAY_TIERS:
        if lanes <= cap:
            return cols, rows
    return 4, 3


def _pick_decoder(repo: ProductRepo, screens: int) -> dict | None:
    """按电视墙块数选解码器：4 块 / 9 块 / 20 路一台，超出按台数叠加。"""
    for outputs, classes, pattern, require_price in (
        (4, ["中心控制"], "DH-NVD0402FX", True),
        (9, ["中心控制"], "DH-NVD0904FX", True),
        (20, ["NVD-P系列"], "DH-NVD2005DU", False),
    ):
        if screens <= outputs:
            p = repo.pick_model(classes, ["解码器"], pattern,
                                require_price=require_price)
            if p:
                return {**p, "qty": 1, "outputs": outputs}
    p = repo.pick_model(["NVD-P系列"], ["解码器"], "DH-NVD2005DU", require_price=False)
    if p:
        return {**p, "qty": math.ceil(screens / 20), "outputs": 20}
    return None


def detect_industry(text: str) -> tuple[str, list[str]]:
    """按加权关键词判行业；识别不出来时回落默认行业并交给 analyze 记缺口。"""
    industry, _info = industry_of(text)
    tags = ["小区社区"] if industry == "智慧社区" else []
    return industry, tags


def match_scenes(text: str, industry: str | None = None) -> list[dict]:
    """行业感知的场景匹配。

    - 行业专属场景只在对应行业下参与匹配，避免「化工园区」把电力场景带进来；
    - 更具体的场景可压掉泛化场景（如 输电线路监测 压掉 周界围墙）；
    - 兜底场景（公共区域）只有在原文明确给了点位数时才保留，避免抢路数。
    """
    if industry is None:
        industry, _ = industry_of(text)
    matched = [
        r for r in SCENE_RULES
        if any(k in text for k in r["keys"])
        and (not r["industries"] or industry in r["industries"])
    ]
    names = {r["name"] for r in matched}
    dropped: set[str] = set()
    for rule in matched:
        dropped.update(s for s in rule["suppresses"] if s in names)
    matched = [r for r in matched if r["name"] not in dropped]
    if len(matched) > 1:
        explicit = explicit_lanes(text, matched)
        matched = [r for r in matched
                   if not r["fallback_only_with_lanes"] or explicit.get(r["name"])]
    return matched or [scene_def(FALLBACK_SCENE)]


def _first_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


# 路数写法：40路 / 40 个点位 / 40台摄像机 / 40个
_LANE_UNIT = re.compile(r"(\d+)\s*(?:路|个点位|台摄像机|摄像机|台|个)")
_CLAUSE_SEP = "，,。；;、\n\r"
_MAX_LANE_GAP = 8  # 场景词与数字之间最多隔几个字，超过就认为不是同一个短语


def explicit_lanes(text: str, scenes: list[dict]) -> dict[str, int]:
    """把「场景词 + 数字」短语里的路数归给最近的那个场景，同一场景多处出现累加。

    只认数字前 8 个字内、且中间没有标点的最近场景词：
    「园区周界 80 路」归周界而不是园区；「办公区周界 40 路」也不会同时算给室内区域和周界。
    """
    hits: list[tuple[int, int, str]] = []
    for rule in scenes:
        for key in rule["keys"]:
            pos = text.find(key)
            while pos >= 0:
                hits.append((pos, pos + len(key), rule["name"]))
                pos = text.find(key, pos + 1)

    alloc: dict[str, int] = {}
    for m in _LANE_UNIT.finditer(text):
        npos = m.start()
        owner: tuple[int, int, str] | None = None
        for start, end, name in hits:
            if end > npos or npos - end > _MAX_LANE_GAP:
                continue
            if any(ch in _CLAUSE_SEP for ch in text[end:npos]):
                continue
            if owner is None or end > owner[1]:
                owner = (start, end, name)
        if owner:
            alloc[owner[2]] = alloc.get(owner[2], 0) + int(m.group(1))
    return alloc


def allocate_lanes(text: str, scenes: list[dict], total: int) -> tuple[dict[str, int], list[str], list[str]]:
    """返回 (场景→路数, 缺口, 假设)。"""
    gaps: list[str] = []
    assumptions: list[str] = []
    explicit = explicit_lanes(text, scenes)
    explicit = {k: v for k, v in explicit.items() if v}
    if explicit:
        remaining = max(0, total - sum(explicit.values()))
        missing = [r["name"] for r in scenes if r["name"] not in explicit]
        alloc = dict(explicit)
        if missing and remaining:
            share = remaining // len(missing)
            for name in missing:
                alloc[name] = share
                remaining -= share
        for name in missing:
            alloc.setdefault(name, 0)
        if remaining:
            first = scenes[0]["name"]
            alloc[first] = alloc.get(first, 0) + remaining
    else:
        share = total // len(scenes)
        alloc = {r["name"]: share for r in scenes}
        left = total - share * len(scenes)
        alloc[scenes[0]["name"]] += left
        if len(scenes) > 1:
            assumptions.append(f"未提供各场景点位数，{total} 路按场景平均分配")

    zero = [n for n, v in alloc.items() if v <= 0]
    if zero:
        gaps.append(f"以下场景点位数未确定：{'、'.join(zero)}")
    return alloc, gaps, assumptions


@dataclass
class Plan:
    """意图识别 Agent 的产物：行业、场景、路数分配与需求条目（不含产品）。"""

    text: str
    industry: str
    tags: list[str]
    industries: list[str]
    scenes: list[dict]
    total: int
    days: int
    alloc: dict[str, int]
    requirements: list[Requirement]
    scale: Scale
    gaps: list[str]
    assumptions: list[str]

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "industry": self.industry,
            "tags": self.tags,
            "industries": self.industries,
            "scenes": self.scenes,
            "total": self.total,
            "days": self.days,
            "alloc": self.alloc,
            "requirements": [r.model_dump() for r in self.requirements],
            "scale": self.scale.model_dump(),
            "gaps": self.gaps,
            "assumptions": self.assumptions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        return cls(
            text=data.get("text", ""),
            industry=data.get("industry", ""),
            tags=list(data.get("tags") or []),
            industries=list(data.get("industries") or []),
            scenes=list(data.get("scenes") or []),
            total=int(data.get("total") or 0),
            days=int(data.get("days") or DEFAULT_RETENTION_DAYS),
            alloc=dict(data.get("alloc") or {}),
            requirements=[Requirement(**r) for r in data.get("requirements") or []],
            scale=Scale(**(data.get("scale") or {})),
            gaps=list(data.get("gaps") or []),
            assumptions=list(data.get("assumptions") or []),
        )


@dataclass
class Bom:
    """配单 Agent 的产物：设备清单 + 存储/带宽测算。"""

    devices: list[Device]
    storage: dict
    bandwidth: float
    lanes_total: int
    gaps: list[str]
    assumptions: list[str]

    def to_dict(self) -> dict:
        return {
            "devices": [d.model_dump() for d in self.devices],
            "storage": self.storage,
            "bandwidth": self.bandwidth,
            "lanes_total": self.lanes_total,
            "gaps": self.gaps,
            "assumptions": self.assumptions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Bom":
        return cls(
            devices=[Device(**d) for d in data.get("devices") or []],
            storage=dict(data.get("storage") or {}),
            bandwidth=float(data.get("bandwidth") or 0),
            lanes_total=int(data.get("lanes_total") or 0),
            gaps=list(data.get("gaps") or []),
            assumptions=list(data.get("assumptions") or []),
        )


def analyze(
    text: str,
    lanes: int | None = None,
    retention_days: int | None = None,
) -> Plan:
    """意图识别：行业路由、场景识别、路数分配，产出结构化建设意图。"""
    industry, info = industry_of(text)
    tags = ["小区社区"] if industry == "智慧社区" else []
    industries = bom_industries(industry, tags)
    scenes = match_scenes(text, industry)

    parsed_total = _first_int(r"(\d+)\s*(?:路|个点位|台摄像机|摄像机|点位|个监控)", text)
    total = lanes or parsed_total or DEFAULT_LANES
    gaps: list[str] = []
    assumptions = ["码流按场景经验值估算，交付前需按实际设备型号核对"]
    if info["fallback"]:
        gaps.append(
            f"需求未体现行业特征，已按「{industry}」通用口径测算；"
            "如属电力、交通、化工、医疗等专业行业，请补充行业与场景"
        )
    if not lanes and not parsed_total:
        gaps.append(f"需求未写点位数，按默认 {DEFAULT_LANES} 路估算（可通过 --lanes 调整）")

    parsed_days = _first_int(r"(\d+)\s*天", text)
    days = retention_days or parsed_days or DEFAULT_RETENTION_DAYS
    if not retention_days and not parsed_days:
        gaps.append(f"需求未写存储天数，按默认 {DEFAULT_RETENTION_DAYS} 天测算（可通过 --days 调整）")

    alloc, alloc_gaps, alloc_assumptions = allocate_lanes(text, scenes, total)
    gaps.extend(alloc_gaps)
    assumptions.extend(alloc_assumptions)

    requirements = [
        Requirement(
            target=r["name"],
            intent=r["intent"],
            level=r["level"],
            lanes=alloc.get(r["name"], 0),
            bitrate_mbps=r["bitrate"],
        )
        for r in scenes
        if alloc.get(r["name"], 0) > 0
    ]
    if not requirements:
        raise SpecGateError(["没有任何可用点位，无法生成方案"])

    scale = Scale(
        buildings=_first_int(r"(\d+)\s*栋", text) or 0,
        area_ha=float(_first_int(r"(\d+)\s*公顷", text) or 0),
        entrances=_first_int(r"(\d+)\s*(?:个)?(?:大门|出入口)", text) or 0,
    )
    return Plan(text=text, industry=industry, tags=tags, industries=industries,
                scenes=scenes, total=total, days=days, alloc=alloc,
                requirements=requirements, scale=scale, gaps=gaps,
                assumptions=assumptions)


def _pick_by_classes(
    repo: ProductRepo,
    classes: list[str],
    keywords: list[str],
    industries: list[str],
    require_price: bool,
) -> tuple[dict | None, str]:
    """按类目优先级依次取型号：先方案库配单，再产品库直配。

    类目顺序即优先级，所以「测温型枪机 → 通用枪机」排在前面的
    行业专属机型会先被选中，选不到才落到通用类目。
    """
    for cls in classes:
        hit = repo.pick_from_boms(industries, [cls], keywords)
        if hit and hit.get("price_stand") is not None:
            return hit, "方案库配单"
        hit = repo.pick_model([cls], keywords, require_price=require_price)
        if hit:
            return hit, "产品库类目"
    return None, ""


def select_cameras(repo: ProductRepo, plan: Plan) -> list[Device]:
    """选型 Agent：通用场景走 RAG，行业专属场景走类目优先级 + 缺货策略。"""
    devices: list[Device] = []
    for req in plan.requirements:
        rule = scene_def(req.target)
        classes = rule["camera_classes"]
        keywords = rule["camera_keywords"]
        require_price = rule["require_price"]
        generic = set(classes) <= GENERIC_CAMERA_CLASSES

        p: dict | None = None
        refs = 0
        basis = ""
        if generic:
            sel = select_camera(
                repo, rule["rag_family"] or req.target, intent=req.intent,
                industries=plan.industries, classes=classes, keywords=keywords,
            )
            if sel:
                p = sel["product"]
                refs = sel["refs"]
                basis = sel["basis"]

        if p is None:
            p, where = _pick_by_classes(repo, classes, keywords,
                                        plan.industries, require_price)
            if p:
                basis = f"{where}按类目匹配（{'/'.join(classes)}）"

        # 强专属场景：拿到通用机型等同于没找到，交给缺货策略处理
        if p is not None and rule["exclusive"] and p.get("class_name") in GENERIC_CAMERA_CLASSES:
            p = None

        degraded = ""
        if p is None:
            if rule["degrade"] == "report":
                raise SpecGateError([
                    f"「{req.target}」需要行业专属设备（{'/'.join(classes)}），"
                    "产品库中暂无可报价型号；该场景不做通用设备替代，请人工确认型号"
                ])
            fb_classes = [c for c in classes if c in GENERIC_CAMERA_CLASSES] or ["网络摄像机"]
            p = repo.pick_model(fb_classes, ["400万"])
            if not p:
                raise SpecGateError([f"产品库缺少 {req.target} 可用摄像机"])
            degraded = ("专属设备缺失，已降级为通用机型，"
                        "需人工确认是否满足行业要求")
            basis = f"产品库通用类目兜底（{'/'.join(fb_classes)}）"
            plan.assumptions.append(f"{req.target}：{degraded}")

        if refs and p.get("year_new"):
            basis += f"（{p['year_new']} 上架）"
        reason = (f"{req.target} {req.lanes} 路 · {req.intent}；"
                  f"码流 {req.bitrate_mbps:g} Mbps；{basis}")
        if degraded:
            reason += f"；注意：{degraded}"
        devices.append(Device(
            category=rule["device_category"],
            model=p["model"],
            qty=req.lanes,
            scene=req.target,
            display_name=p["display_name"],
            unit_price=p["price_stand"],
            reason=reason,
        ))
    return devices


def plan_bom(repo: ProductRepo, plan: Plan, cameras: list[Device]) -> Bom:
    """配单 Agent：存储/带宽测算 + NVR/硬盘/交换/显示/平台的数量与型号。"""
    requirements = plan.requirements
    devices: list[Device] = list(cameras)
    gaps: list[str] = []
    assumptions: list[str] = []

    storage = calc_storage(requirements, plan.days)
    bandwidth = calc_bandwidth(requirements)
    lanes_total = total_lanes(requirements)

    nvr = repo.pick_model(["通用存储录像机"], ["网络硬盘录像机"], model_pattern="4432")
    if not nvr:
        raise SpecGateError(["产品库缺少可用的网络硬盘录像机"])
    nvr_qty = math.ceil(lanes_total / NVR_CHANNELS)
    devices.append(Device(
        category="NVR",
        model=nvr["model"],
        qty=nvr_qty,
        scene="监控中心",
        display_name=nvr["display_name"],
        unit_price=nvr["price_stand"],
        reason=f"总点位 {lanes_total} 路 ÷ {NVR_CHANNELS} 路/台 = {nvr_qty} 台",
    ))

    disk = repo.pick_model(["机械硬盘"], ["8TB"], model_pattern="ST8000")
    if not disk:
        raise SpecGateError(["产品库缺少 8TB 监控硬盘"])
    disk_qty = max(1, math.ceil(storage["with_redundancy_tb"] / DISK_CAPACITY_TB))
    devices.append(Device(
        category="机械硬盘",
        model=disk["model"],
        qty=disk_qty,
        scene="监控中心",
        display_name=disk["display_name"],
        unit_price=disk["price_stand"],
        reason=f"冗余容量 {storage['with_redundancy_tb']} TB ÷ "
               f"{DISK_CAPACITY_TB} TB/块 = {disk_qty} 块",
    ))

    switch = repo.pick_model(["数通产品(专项)"], ["24口千兆电口", "PoE", "交换机"])
    if switch:
        switch_qty = math.ceil(lanes_total / SWITCH_PORTS)
        devices.append(Device(
            category="PoE交换机",
            model=switch["model"],
            qty=switch_qty,
            scene="传输",
            display_name=switch["display_name"],
            unit_price=switch["price_stand"],
            reason=f"{lanes_total} 路 ÷ {SWITCH_PORTS} 口/台 = {switch_qty} 台，"
                   f"摄像机 PoE 供电",
        ))
    else:
        gaps.append("产品库缺少 24 口 PoE 交换机，传输层待补")

    monitor = repo.pick_model(["渠道商显"], ["55英寸", "监视器"], "DH-LM55")
    if not monitor:
        monitor = repo.pick_model(["渠道商显"], ["监视器"], "DH-LM")
    cols, rows = display_plan(lanes_total)
    screens = cols * rows
    if screens == 1 and monitor:
        devices.append(Device(
            category="监视器",
            model=monitor["model"],
            qty=1,
            scene="监控中心",
            display_name=monitor["display_name"],
            unit_price=monitor["price_stand"],
            reason=f"{lanes_total} 路（≤16）单屏上墙，55 英寸监视器 1 台",
        ))
    elif screens > 1:
        wall = repo.pick_model(["55寸拼接"], ["1.7mm", "标亮"], "DH-LS550UDM",
                               require_price=False)
        if not wall:
            wall = repo.pick_model(["55寸拼接"], [], None, require_price=False)
        if wall:
            devices.append(Device(
                category="拼接屏",
                model=wall["model"],
                qty=screens,
                scene="监控中心",
                display_name=wall["display_name"],
                unit_price=wall["price_stand"],
                reason=f"{lanes_total} 路 → {cols}×{rows} 电视墙，"
                       f"{screens} 块 55 英寸拼接单元",
            ))
            bracket = repo.pick_model(["丰视液晶"], ["55英寸"], "FS-A055",
                                      require_price=False)
            if bracket:
                devices.append(Device(
                    category="拼接支架",
                    model=bracket["model"],
                    qty=screens,
                    scene="监控中心",
                    display_name=bracket["display_name"],
                    unit_price=bracket["price_stand"],
                    reason=f"{screens} 块拼接单元配套前维护支架",
                ))
            decoder = _pick_decoder(repo, screens)
            if decoder:
                devices.append(Device(
                    category="解码器",
                    model=decoder["model"],
                    qty=decoder["qty"],
                    scene="监控中心",
                    display_name=decoder["display_name"],
                    unit_price=decoder["price_stand"],
                    reason=f"{screens} 块屏由 {decoder['qty']} 台 "
                           f"{decoder['outputs']} 路解码器上墙",
                ))
            else:
                gaps.append("产品库缺少上墙解码器，显示层待补")
        else:
            gaps.append("产品库缺少 55 寸拼接单元，显示层待补")
    else:
        gaps.append("产品库缺少监控中心监视器，显示层待补")

    platform_devices, platform_gaps, platform_notes = build_platform(
        repo, lanes_total, nvr_qty, plan.industry)
    devices.extend(platform_devices)
    gaps.extend(platform_gaps)
    assumptions.extend(platform_notes)

    assumptions.extend([
        f"存储按 {plan.days} 天、H.265、{storage['redundancy']} 冗余测算",
        f"NVR 按 {NVR_CHANNELS} 路/台、硬盘按 {DISK_CAPACITY_TB}TB/块折算",
        "上行带宽按路数码流之和 × 1.2 并发系数估算",
    ])
    if screens > 1:
        assumptions.append(
            f"电视墙按总点位 {lanes_total} 路分级为 {cols}×{rows}，"
            "拼接单元与支架无标准价，需商务询价"
        )

    return Bom(devices=devices, storage=storage, bandwidth=bandwidth,
               lanes_total=lanes_total, gaps=gaps, assumptions=assumptions)


def assemble_spec(plan: Plan, bom: Bom, name: str | None = None) -> Spec:
    """方案 Agent：把意图与配单组装成可审阅、可导出的 Spec。"""
    return Spec(
        project=Project(
            name=name or f"{plan.industry}综合安防项目",
            industry=plan.industry,
            industry_tags=plan.tags + [r["name"] for r in plan.scenes],
            scale=plan.scale,
        ),
        requirements=plan.requirements,
        devices=bom.devices,
        storage=Storage(
            retention_days=plan.days,
            bitrate_mbps={r.target: r.bitrate_mbps for r in plan.requirements},
            **bom.storage,
        ),
        network=Network(uplink_mbps=bom.bandwidth),
        boundaries=Boundaries(
            included=["前端摄像机及配套支架", "PoE 交换与传输链路", "NVR 存储与硬盘",
                      "监控中心电视墙（拼接屏/监视器 + 解码上墙）"],
            excluded=["土建与立杆基础", "供电引入", "第三方平台对接", "机房空调与消防"],
            assumptions=plan.assumptions + bom.assumptions,
        ),
        gaps=plan.gaps + bom.gaps,
    )


def build_spec(
    text: str,
    name: str | None = None,
    lanes: int | None = None,
    retention_days: int | None = None,
    repo: ProductRepo | None = None,
    plan: Plan | None = None,
    cameras: list[Device] | None = None,
    bom: Bom | None = None,
) -> Spec:
    """一站式构建；也可传入各子 Agent 的中间产物，避免重复计算。"""
    own_repo = repo is None
    repo = repo or ProductRepo()
    try:
        plan = plan or analyze(text, lanes, retention_days)
        cameras = list(cameras) if cameras is not None else select_cameras(repo, plan)
        bom = bom or plan_bom(repo, plan, cameras)
        return assemble_spec(plan, bom, name)
    finally:
        if own_repo:
            repo.close()


def validate_spec(spec: Spec, allowed_models: set[str] | None = None) -> None:
    """硬闸门：型号必须命中产品白名单，数量必须为正。"""
    if allowed_models is None:
        with ProductRepo() as repo:
            allowed_models = repo.allowed_models()
    gaps: list[str] = []
    for d in spec.devices:
        if d.model not in allowed_models:
            gaps.append(f"型号不在产品白名单：{d.model}（{d.category}）")
        if d.qty <= 0:
            gaps.append(f"{d.category} 数量非法：{d.qty}")
    if spec.storage.raw_tb <= 0:
        gaps.append("存储容量测算为 0")
    if gaps:
        raise SpecGateError(gaps)
