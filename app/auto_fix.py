"""工程审查后的自动修正建议。

只自动处理有确定输入的情况；涉及存储架构、盘位和协议的事项不擅自改型号，
而是给出可执行建议交给人工确认。
"""
from __future__ import annotations

import math
import re


def storage_patch(spec: dict) -> dict:
    """对可确定的 NVR 盘位不足问题生成设备数量修正。"""
    devices = spec.get("devices") or []
    nvr_idx = next((i for i, d in enumerate(devices)
                    if d.get("category") in ("NVR", "通用存储录像机")), None)
    disk_qty = sum(int(d.get("qty") or 0) for d in devices
                   if d.get("category") in ("机械硬盘", "硬盘"))
    if nvr_idx is None or disk_qty <= 0:
        return {}
    nvr = devices[nvr_idx]
    text = " ".join(str(nvr.get(k) or "") for k in ("model", "display_name"))
    match = re.search(r"(?:HDS?|盘位|硬盘位)[-_ ]?(\d{1,2})", text, re.I)
    slots = int(match.group(1)) if match else None
    if not slots:
        return {}
    current = int(nvr.get("qty") or 0)
    target = max(current, math.ceil(disk_qty / slots))
    if target <= current:
        return {}
    return {"index": nvr_idx + 1, "qty": target, "slots": slots,
            "disk_qty": disk_qty, "old_qty": current}


def suggestions(validation: dict) -> dict:
    items = validation.get("items") or []
    out = []
    for item in items:
        if item.get("level") != "block":
            continue
        code = item.get("code")
        if code == "disk_slots":
            out.append("存储架构需要人工确认：改用大盘位录像机/集中式存储，或重新核定硬盘数量；系统不会擅自替换型号。")
        elif code == "input":
            out.append("请在需求描述或手动编辑区明确点位数和录像保存天数，再重新计算。")
        elif code == "nvr_capacity":
            out.append("请人工确认 NVR 接入通道和带宽，必要时更换更高规格型号。")
        elif code == "switch_ports":
            out.append("请人工确认交换机端口与 PoE 功率，必要时增加交换机或调整端口规格。")
        else:
            out.append(item.get("action") or item.get("detail") or item.get("title") or "请人工确认该工程问题")
    return {"applied": False, "safe": False, "suggestions": out,
            "message": "当前阻断项涉及工程架构，AI 不会擅自改动型号或容量。" if out else "没有可自动修正的阻断项。"}
