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

DEFAULT_LANES = 50
DEFAULT_RETENTION_DAYS = 30
NVR_CHANNELS = 32
DISK_CAPACITY_TB = 8
SWITCH_PORTS = 24

# 场景规则：顺序即优先级；关键词命中即视为该场景存在
SCENE_RULES = [
    {
        "name": "周界围墙",
        "keys": ["周界", "围墙", "边界"],
        "intent": "入侵检测",
        "level": "高",
        "bitrate": 4.0,
        "camera_keywords": ["枪型", "400万"],
        "camera_classes": ["网络摄像机"],
    },
    {
        "name": "出入口",
        "keys": ["出入口", "大门", "门禁", "闸机"],
        "intent": "人车通行管理",
        "level": "高",
        "bitrate": 6.0,
        "camera_keywords": ["枪型", "400万"],
        "camera_classes": ["网络摄像机"],
    },
    {
        "name": "主干道",
        "keys": ["主干道", "道路", "路面", "车道", "卡口"],
        "intent": "车辆识别",
        "level": "中",
        "bitrate": 6.0,
        "camera_keywords": ["球机", "400万"],
        "camera_classes": ["分销球机", "网络摄像机"],
    },
    {
        "name": "电梯轿厢",
        "keys": ["电梯", "轿厢", "入梯"],
        "intent": "电瓶车入梯检测",
        "level": "中",
        "bitrate": 2.0,
        "camera_keywords": ["半球", "400万"],
        "camera_classes": ["网络摄像机"],
    },
    {
        "name": "室内区域",
        "keys": ["室内", "大厅", "走廊", "办公", "教室", "会议室",
                 "教学楼", "宿舍", "食堂", "谈话室", "机房", "办案区"],
        "intent": "人员活动监控",
        "level": "中",
        "bitrate": 2.0,
        "camera_keywords": ["海螺", "400万"],
        "camera_classes": ["网络摄像机"],
    },
    {
        "name": "停车场",
        "keys": ["停车场", "车棚", "车位", "车库", "地库"],
        "intent": "车辆停放管理",
        "level": "中",
        "bitrate": 4.0,
        "camera_keywords": ["球机", "400万"],
        "camera_classes": ["分销球机", "网络摄像机"],
    },
    {
        "name": "公共区域",
        "keys": ["公共区域", "广场", "园区", "小区", "操场"],
        "intent": "公共区域视频监控",
        "level": "中",
        "bitrate": 4.0,
        "camera_keywords": ["枪型", "400万"],
        "camera_classes": ["网络摄像机"],
    },
]


class SpecGateError(Exception):
    """硬闸门：型号不在白名单或关键信息缺失时终止，不输出半成品方案。"""

    def __init__(self, gaps: list[str]):
        self.gaps = gaps
        super().__init__("；".join(gaps))


# 方案库行业口径：53 个配单只覆盖 20 个行业，园区/交管这类统称需要映射后才命中
BOM_INDUSTRY_MAP = {
    "智慧园区": ["工厂园区", "小区社区", "景区场馆", "商业综合体"],
    "智慧交管": ["智能交通", "出入口"],
}

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
    if any(k in text for k in ["交管", "交通", "道路", "交警", "卡口"]):
        industry = "智慧交管"
    elif any(k in text for k in ["校园", "学校", "中学", "小学", "大学", "校区", "教室", "宿管"]):
        industry = "智慧校园"
    else:
        industry = "智慧园区"
    tags = []
    if any(k in text for k in ["小区", "社区", "物业"]):
        tags.append("小区社区")
    return industry, tags


def match_scenes(text: str) -> list[dict]:
    matched = [r for r in SCENE_RULES if any(k in text for k in r["keys"])]
    if len(matched) > 1:
        # 公共区域是兜底场景：「园区/小区」这类字眼经常只是地点修饰，
        # 只有原文明确给了它的点位数才保留，避免抢走别的场景的需求
        explicit = explicit_lanes(text, matched)
        matched = [r for r in matched if r["name"] != "公共区域" or explicit.get("公共区域")]
    return matched or [SCENE_RULES[-1]]


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
    industry, tags = detect_industry(text)
    industries = bom_industries(industry, tags)
    scenes = match_scenes(text)

    parsed_total = _first_int(r"(\d+)\s*(?:路|个点位|台摄像机|摄像机|点位|个监控)", text)
    total = lanes or parsed_total or DEFAULT_LANES
    gaps: list[str] = []
    assumptions = ["码流按场景经验值估算，交付前需按实际设备型号核对"]
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


def select_cameras(repo: ProductRepo, plan: Plan) -> list[Device]:
    """选型 Agent：每个场景按 RAG 案例 → 方案配单 → 产品库规则选摄像机。"""
    devices: list[Device] = []
    for req in plan.requirements:
        rule = next(r for r in plan.scenes if r["name"] == req.target)
        sel = select_camera(
            repo, req.target, intent=req.intent, industries=plan.industries,
            classes=rule["camera_classes"], keywords=rule["camera_keywords"],
        )
        if sel:
            p = sel["product"]
            refs = sel["refs"]
            basis = sel["basis"]
        else:
            p = repo.pick_from_boms(plan.industries, rule["camera_classes"],
                                    rule["camera_keywords"])
            refs = p["refs"] if p else 0
            if not p:
                p = repo.pick_model(rule["camera_classes"], rule["camera_keywords"])
            if refs:
                where = "同行业" if p.get("scope") == "industry" else "同类项目"
                basis = f"方案库{where}配单引用 {refs} 次"
            else:
                basis = "产品库按类目 + 关键词匹配"
        if not p:
            raise SpecGateError([f"产品库缺少 {req.target} 场景可用的摄像机（关键词：{'/'.join(rule['camera_keywords'])}）"])
        if refs and p.get("year_new"):
            basis += f"（{p['year_new']} 上架）"
        devices.append(Device(
            category="网络摄像机",
            model=p["model"],
            qty=req.lanes,
            scene=req.target,
            display_name=p["display_name"],
            unit_price=p["price_stand"],
            reason=f"{req.target} {req.lanes} 路 · {req.intent}；"
                   f"码流 {req.bitrate_mbps:g} Mbps；{basis}",
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
