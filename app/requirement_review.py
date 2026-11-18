"""需求确认清单：在进入 Agent 前，给售前一个可审阅的输入质量检查。"""
from __future__ import annotations

import re

from app.builder import match_scenes
from app.taxonomy import industry_of

_LANE_RE = re.compile(r"(\d+)\s*(?:路|个点位|点位|台摄像机|个摄像机)")
_DAY_RE = re.compile(r"(\d+)\s*(?:天|日)")


def _item(key: str, label: str, status: str, detail: str, priority: str = "normal") -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail,
            "priority": priority}


def assess(text: str, lanes: int | None = None, retention_days: int | None = None,
           project_name: str | None = None) -> dict:
    """只做确定性检查，不调用模型、不访问数据库。"""
    source = (text or "").strip()
    lane_values = [int(x) for x in _LANE_RE.findall(source)]
    detected_lanes = sum(lane_values) if lane_values else None
    days_match = _DAY_RE.search(source)
    detected_days = int(days_match.group(1)) if days_match else None
    scenes = match_scenes(source)
    industry, info = industry_of(source)
    items = []

    effective_lanes = lanes or detected_lanes
    if effective_lanes and effective_lanes > 0:
        origin = "表单回填" if lanes else "需求原文"
        items.append(_item("lanes", "点位规模", "confirmed",
                           f"已确认 {effective_lanes} 路（来源：{origin}）"))
    else:
        items.append(_item("lanes", "点位规模", "confirm",
                           "尚未确认总路数，无法可靠完成设备数量和容量测算", "high"))

    effective_days = retention_days or detected_days
    if effective_days and effective_days > 0:
        origin = "表单回填" if retention_days else "需求原文"
        items.append(_item("retention_days", "录像保存", "confirmed",
                           f"已确认保存 {effective_days} 天（来源：{origin}）"))
    else:
        items.append(_item("retention_days", "录像保存", "confirm",
                           "尚未确认保存天数，硬盘和服务器容量暂不能定稿", "high"))

    if scenes:
        names = "、".join(x["name"] for x in scenes)
        items.append(_item("scenes", "建设场景", "confirmed", f"已识别：{names}"))
    else:
        items.append(_item("scenes", "建设场景", "risk",
                           "未识别到明确场景，请补充区域或业务用途", "high"))

    if project_name or re.search(r"(?:项目|小区|园区|学校|医院|变电站)", source):
        items.append(_item("project", "项目对象", "confirmed", "已识别项目对象"))
    else:
        items.append(_item("project", "项目对象", "confirm",
                           "项目名称或建设对象不明确，建议补充项目名称", "normal"))

    if info["score"] > 0:
        items.append(_item("industry", "行业口径", "confirmed",
                           f"初步判定为 {industry}（命中：{'、'.join(info['hits'])}）"))
    else:
        items.append(_item("industry", "行业口径", "confirm",
                           "未识别到行业特征，选型和方案口径需要人工确认", "normal"))

    counts = {s: sum(1 for x in items if x["status"] == s) for s in ("confirmed", "confirm", "risk")}
    return {"ready": counts["risk"] == 0 and counts["confirm"] == 0,
            "summary": counts, "items": items,
            "detected": {"lanes": detected_lanes, "retention_days": detected_days,
                          "scenes": [x["name"] for x in scenes], "industry": industry}}
