"""规则版「需求 → Spec」构建器（无 LLM Key 时的 P0 实现）。

流程：解析需求文本 → 行业路由 → 场景识别与路数分配 → 选型（只从产品白名单里挑）
→ 确定性计算存储/带宽 → 硬闸门校验。
LLM 接入后只替换「解析需求文本」这一步，后续计算与选型保持不变。
"""
from __future__ import annotations

import math
import re

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
        "keys": ["室内", "大厅", "走廊", "办公", "教室", "会议室"],
        "intent": "人员活动监控",
        "level": "中",
        "bitrate": 2.0,
        "camera_keywords": ["海螺", "400万"],
        "camera_classes": ["网络摄像机"],
    },
    {
        "name": "停车场",
        "keys": ["停车场", "车棚", "车位"],
        "intent": "车辆停放管理",
        "level": "中",
        "bitrate": 4.0,
        "camera_keywords": ["球机", "400万"],
        "camera_classes": ["分销球机", "网络摄像机"],
    },
    {
        "name": "公共区域",
        "keys": ["公共区域", "广场", "园区", "小区"],
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
        matched = [r for r in matched if r["name"] != "公共区域"]
    return matched or [SCENE_RULES[-1]]


def _first_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def _explicit_lanes(text: str, rule: dict) -> int | None:
    for key in rule["keys"]:
        m = re.search(
            re.escape(key) + r"[^0-9，,。；;]{0,6}(\d+)\s*(?:路|个点位|台|个)",
            text,
        )
        if m:
            return int(m.group(1))
    return None


def allocate_lanes(text: str, scenes: list[dict], total: int) -> tuple[dict[str, int], list[str], list[str]]:
    """返回 (场景→路数, 缺口, 假设)。"""
    gaps: list[str] = []
    assumptions: list[str] = []
    explicit = {r["name"]: _explicit_lanes(text, r) for r in scenes}
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


def build_spec(
    text: str,
    name: str | None = None,
    lanes: int | None = None,
    retention_days: int | None = None,
    repo: ProductRepo | None = None,
) -> Spec:
    own_repo = repo is None
    repo = repo or ProductRepo()
    try:
        industry, tags = detect_industry(text)
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

        devices: list[Device] = []
        for req in requirements:
            rule = next(r for r in scenes if r["name"] == req.target)
            p = repo.pick_model(rule["camera_classes"], rule["camera_keywords"])
            if not p:
                raise SpecGateError([f"产品库缺少 {req.target} 场景可用的摄像机（关键词：{'/'.join(rule['camera_keywords'])}）"])
            devices.append(Device(
                category="网络摄像机",
                model=p["model"],
                qty=req.lanes,
                scene=req.target,
                display_name=p["display_name"],
                unit_price=p["price_stand"],
            ))

        storage = calc_storage(requirements, days)
        bandwidth = calc_bandwidth(requirements)
        lanes_total = total_lanes(requirements)

        nvr = repo.pick_model(["通用存储录像机"], ["网络硬盘录像机"], model_pattern="4432")
        if not nvr:
            raise SpecGateError(["产品库缺少可用的网络硬盘录像机"])
        devices.append(Device(
            category="NVR",
            model=nvr["model"],
            qty=math.ceil(lanes_total / NVR_CHANNELS),
            scene="监控中心",
            display_name=nvr["display_name"],
            unit_price=nvr["price_stand"],
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
        ))

        switch = repo.pick_model(["数通产品(专项)"], ["24口千兆电口", "PoE", "交换机"])
        if switch:
            devices.append(Device(
                category="PoE交换机",
                model=switch["model"],
                qty=math.ceil(lanes_total / SWITCH_PORTS),
                scene="传输",
                display_name=switch["display_name"],
                unit_price=switch["price_stand"],
            ))
        else:
            gaps.append("产品库缺少 24 口 PoE 交换机，传输层待补")

        monitor = repo.pick_model(["渠道商显"], ["监视器"], model_pattern="DH-LM")
        if monitor:
            devices.append(Device(
                category="监视器",
                model=monitor["model"],
                qty=1,
                scene="监控中心",
                display_name=monitor["display_name"],
                unit_price=monitor["price_stand"],
            ))

        # 平台层：从知识库挑选真实大华综合管理平台（监控中心统一管控）
        platform = repo.search(class_name="综合平台", limit=1)
        if platform:
            p = platform[0]
            devices.append(Device(
                category="综合平台",
                model=p["model"],
                qty=1,
                scene="监控中心",
                display_name=p["display_name"],
                unit_price=p.get("price_stand"),
            ))
        else:
            gaps.append("产品库缺少综合管理平台，平台层待补")

        scale = Scale(
            buildings=_first_int(r"(\d+)\s*栋", text) or 0,
            area_ha=float(_first_int(r"(\d+)\s*公顷", text) or 0),
            entrances=_first_int(r"(\d+)\s*(?:个)?(?:大门|出入口)", text) or 0,
        )

        assumptions.extend([
            f"存储按 {days} 天、H.265、{storage['redundancy']} 冗余测算",
            f"NVR 按 {NVR_CHANNELS} 路/台、硬盘按 {DISK_CAPACITY_TB}TB/块折算",
            "上行带宽按路数码流之和 × 1.2 并发系数估算",
        ])

        return Spec(
            project=Project(
                name=name or f"{industry}综合安防项目",
                industry=industry,
                industry_tags=tags + [r["name"] for r in scenes],
                scale=scale,
            ),
            requirements=requirements,
            devices=devices,
            storage=Storage(
                retention_days=days,
                bitrate_mbps={r.target: r.bitrate_mbps for r in requirements},
                **storage,
            ),
            network=Network(uplink_mbps=bandwidth),
            boundaries=Boundaries(
                included=["前端摄像机及配套支架", "PoE 交换与传输链路", "NVR 存储与硬盘", "监控中心显示"],
                excluded=["土建与立杆基础", "供电引入", "第三方平台对接", "机房空调与消防"],
                assumptions=assumptions,
            ),
            gaps=gaps,
        )
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
