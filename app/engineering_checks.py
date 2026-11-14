"""方案工程可信度检查。

这里不替代 builder/calc 的确定性计算，只回答一个售前问题：
当前结果哪些可以直接拿去内部评审，哪些仍需要现场、厂家或商务确认。
"""
from __future__ import annotations

import re


def _item(code: str, level: str, title: str, detail: str, action: str = "") -> dict:
    return {"code": code, "level": level, "title": title,
            "detail": detail, "action": action}


def evaluate(spec: dict, text: str = "") -> dict:
    spec = spec or {}
    requirements = spec.get("requirements") or []
    devices = spec.get("devices") or []
    storage = spec.get("storage") or {}
    network = spec.get("network") or {}
    gaps = spec.get("gaps") or []
    total_lanes = sum(int(r.get("lanes") or 0) for r in requirements)
    camera_qty = sum(int(d.get("qty") or 0) for d in devices
                     if d.get("category") == "网络摄像机")
    nvr_qty = sum(int(d.get("qty") or 0) for d in devices
                  if d.get("category") in ("NVR", "通用存储录像机"))
    disk_qty = sum(int(d.get("qty") or 0) for d in devices
                   if d.get("category") in ("机械硬盘", "硬盘"))
    nvr = next((d for d in devices if d.get("category") in ("NVR", "通用存储录像机")), None)
    switch = next((d for d in devices if d.get("category") == "PoE交换机"), None)
    items: list[dict] = []

    explicit_lanes = bool(re.search(r"\d+\s*(?:路|个点位|台摄像机|摄像机)", text or ""))
    explicit_days = bool(re.search(r"\d+\s*天", text or ""))
    if explicit_lanes and explicit_days:
        items.append(_item("input", "pass", "关键输入已明确",
                           f"原文包含点位数与存储天数（{total_lanes} 路 / {storage.get('retention_days', '—')} 天）。"))
    else:
        missing = []
        if not explicit_lanes: missing.append("点位数")
        if not explicit_days: missing.append("存储天数")
        items.append(_item("input", "block", "关键输入存在默认值",
                           "、".join(missing) + "未在原文明确，当前结果不能直接作为正式报价或投标承诺。",
                           "补齐招标条款或现场确认后重新计算"))

    if camera_qty and camera_qty != total_lanes:
        items.append(_item("camera_qty", "warn", "摄像机数量与点位总数不一致",
                           f"需求点位 {total_lanes} 路，当前摄像机设备合计 {camera_qty} 台，可能存在备用、替换或分组口径差异。",
                           "确认点位表与设备数量的对应关系"))
    else:
        items.append(_item("camera_qty", "pass", "前端数量基本一致",
                           f"摄像机设备数量与需求点位均为 {total_lanes} 路。"))

    if nvr_qty and nvr_qty * 32 < total_lanes:
        items.append(_item("nvr_capacity", "block", "NVR 通道数不足",
                           f"按 32 路/台粗校验，{nvr_qty} 台 NVR 不足以承载 {total_lanes} 路。",
                           "调整 NVR 型号或数量，并核对接入带宽"))
    else:
        items.append(_item("nvr_capacity", "warn", "NVR 通道与接入带宽待核验",
                           "当前仅按通道数做粗校验，尚未核对具体型号的接入带宽、协议和解码能力。",
                           "打开产品彩页核对 NVR 通道、接入带宽、编码格式和协议"))

    if nvr and disk_qty:
        nvr_text = " ".join(str(nvr.get(k) or "") for k in ("model", "display_name"))
        slot_match = re.search(r"(?:HDS?|盘位|硬盘位)[-_ ]?(\d{1,2})", nvr_text, re.I)
        slots = int(slot_match.group(1)) if slot_match else None
        if slots and disk_qty > slots * nvr_qty:
            items.append(_item("disk_slots", "block", "硬盘数量超过 NVR 盘位",
                               f"当前 {nvr_qty} 台 NVR 按型号疑似每台 {slots} 盘位，无法容纳 {disk_qty} 块硬盘。",
                               "改用大盘位存储设备、增加录像机或重新设计集中存储"))
        else:
            items.append(_item("disk_slots", "warn", "硬盘盘位需要按彩页确认",
                               f"当前配置 {disk_qty} 块硬盘，程序未能可靠取得 NVR 实际盘位。",
                               "核对 NVR 盘位、单盘容量上限和 RAID 支持"))

    if switch and camera_qty:
        sw_text = " ".join(str(switch.get(k) or "") for k in ("model", "display_name"))
        port_match = re.search(r"(\d{1,3})\s*口", sw_text)
        ports = int(port_match.group(1)) if port_match else None
        if ports and ports * int(switch.get("qty") or 0) < camera_qty:
            items.append(_item("switch_ports", "block", "交换机接入端口不足",
                               f"按 {ports} 口/台粗校验，当前交换机端口不足以接入 {camera_qty} 台摄像机。",
                               "增加交换机或调整端口规格"))
        else:
            items.append(_item("switch_ports", "warn", "交换机端口与 PoE 功率待核验",
                               f"当前配置 {int(switch.get('qty') or 0)} 台交换机，端口数仅做文字规格粗算。",
                               "核对 PoE 总功率、满载功耗、上联口数量和光模块"))

    raw_tb = float(storage.get("raw_tb") or 0)
    usable_tb = float(storage.get("with_redundancy_tb") or 0)
    if raw_tb > 0 and usable_tb >= raw_tb:
        items.append(_item("storage", "warn", "存储容量可计算，但工程口径未闭合",
                           f"当前计算裸容量 {raw_tb:g} TB，含冗余 {usable_tb:g} TB；尚未绑定 RAID、槽位、热备和格式化可用容量。",
                           "确认录像模式、RAID/热备、硬盘槽位和实际可用容量"))
    else:
        items.append(_item("storage", "block", "存储计算结果异常",
                           "未得到有效的裸容量或冗余容量。", "补充码流、录像天数和存储策略"))

    uplink = float(network.get("uplink_mbps") or 0)
    if uplink > 0:
        items.append(_item("network", "warn", "网络只完成带宽粗算",
                           f"当前上行带宽为 {uplink:g} Mbps，尚未校验 PoE 功率、交换机端口、光纤链路、VLAN 和冗余。",
                           "根据点位表完成接入/汇聚/核心网络设计"))
    else:
        items.append(_item("network", "block", "缺少网络带宽结果",
                           "无法进行网络承载校验。", "补充摄像机码流和网络层级"))

    weak = [d.get("model") or d.get("category") for d in devices
            if not (d.get("reason") or "") or not any(x in (d.get("reason") or "")
                                                       for x in ("产品库", "方案库", "能力标签", "RAG"))]
    if weak:
        items.append(_item("evidence", "warn", "部分选型缺少明确证据",
                           "、".join(str(x) for x in weak[:5]) + ("等" if len(weak) > 5 else "") + "未显示产品库/方案库依据。",
                           "补充产品彩页、官网参数或官方配单来源"))
    else:
        items.append(_item("evidence", "pass", "选型理由可追溯",
                           "设备均带有产品库、方案库或能力标签依据。"))

    if gaps:
        items.append(_item("gaps", "block", "方案仍有未闭合缺口",
                           "；".join(str(x) for x in gaps[:6]), "逐条补齐缺口后再进入正式交付"))

    # 交管项目的前端选型并不等于完整交付：违法取证、事件检测、信号控制
    # 往往还涉及平台协议、算法授权和交警平台联调，必须在审查清单中单列。
    industry = str((spec.get("project") or {}).get("industry") or "")
    if "交管" in industry or "交通" in industry:
        items.append(_item(
            "traffic_platform", "warn", "交管平台接口尚未闭合",
            "卡口/电警/事件检测已完成前端类目匹配，但平台协议、算法授权、违法取证字段和联调范围不在本次自动报价内。",
            "确认接入平台、GB/T 28181 或厂家协议、算法授权、信号机/诱导屏接口及联调责任边界",
        ))

    rank = {"pass": 0, "warn": 1, "block": 2}
    worst = max((x["level"] for x in items), key=lambda x: rank[x], default="warn")
    labels = {"pass": "可进入内部评审", "warn": "需要工程复核", "block": "不可直接交付"}
    return {"level": worst, "label": labels[worst], "items": items,
            "summary": {"pass": sum(x["level"] == "pass" for x in items),
                        "warn": sum(x["level"] == "warn" for x in items),
                        "block": sum(x["level"] == "block" for x in items)}}
