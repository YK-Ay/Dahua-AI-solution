"""W4 竞品对比：competitor_facts → 字段映射 / 归一化 → 逐字段 diff + 话术。

原则：
- 只用官网公开参数页里真实存在的字段，缺项标"官网未公开"，不补、不猜。
- 数值先归一化再比较；口径不同（如"数字宽动态"无 dB 值）标"口径不同"，不做断言。
- 每条差异绑定两侧 source_url 与原文证据，导出 DOCX 时一并列出。

用法：
    python -m app.compare --category 网络摄像机
    python -m app.compare --category NVR --docx out.docx
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, RGBColor

from config.settings import DB_PATH
from scraper.density import annotate

from .word import ACCENT, MUTED, _heading, _para, _table

DAHUA, HIK = "大华", "海康"

# 官网同档位代表款（类目级）。补采后同类目有几十款机型，
# _load 必须按型号精确过滤，否则不同机型的同名字段会互相覆盖。
REPRESENTATIVES: dict[str, tuple[str, str]] = {
    "网络摄像机": ("DH-IPC-HFW5443M1-YL-PV-AS", "DS-2CD1245(D)-LA"),
    "NVR": ("DH-NVR5532-I", "DS-7700N-K4-V3"),
    "PoE交换机": ("DH-AS3900C-24GT4GF-370", "DS-3E0526P-E/M"),
}

STATUS_LABEL = {
    "same": "同档",
    "dahua": "大华占优",
    "hik": "海康占优",
    "qualitative": "口径不同",
    "only_dahua": "仅大华公开",
    "only_hik": "仅海康公开",
}


@dataclass
class Parsed:
    num: float | None = None      # 归一化数值，None 表示官网未给出可换算数值
    display: str = ""             # 归一化展示值（用于对照表）
    note: str = ""                # 口径说明（如多口径取最大值）
    tokens: tuple = dc_field(default_factory=tuple)  # 集合/端口等附加比较信息


def _num(pattern: str, text: str, cast=float) -> float | None:
    m = re.search(pattern, text, re.I)
    return cast(m.group(1)) if m else None


def p_pixel(text: str) -> Parsed:
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    if m:
        return Parsed(float(m.group(1)), f"{m.group(1)} 万像素")
    m = re.search(r"(\d+)\s*[×xX*]\s*(\d+)", text)
    if m:
        wan = round(int(m.group(1)) * int(m.group(2)) / 10000, 1)
        return Parsed(wan, f"{m.group(1)}×{m.group(2)}，约 {wan} 万像素")
    return Parsed(None, text)


def p_sensor(text: str) -> Parsed:
    m = re.search(r"1\s*/\s*(\d+(?:\.\d+)?)\s*(?:英寸|\")", text)
    if m:
        return Parsed(round(1 / float(m.group(1)), 4), f"1/{m.group(1)} 英寸")
    return Parsed(None, text)


def p_distance(text: str) -> Parsed:
    vals = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*(?:m|米)(?![a-zA-Z])", text)]
    if vals:
        return Parsed(max(vals), f"{max(vals):g} m")
    return Parsed(None, text)


def p_db(text: str) -> Parsed:
    v = _num(r"(\d+(?:\.\d+)?)\s*dB", text)
    return Parsed(v, f"{v:g} dB" if v is not None else text)


def p_ip(text: str) -> Parsed:
    m = re.search(r"IP\s*([0-9]{2})", text, re.I)
    if m:
        return Parsed(float(m.group(1)), f"IP{m.group(1)}")
    return Parsed(None, text)


def p_poe(text: str) -> Parsed:
    low = text.lower()
    for rank, label in ((3, "802.3bt"), (2, "802.3at"), (1, "802.3af")):
        if label in low:
            suffix = "（PoE+）" if rank == 2 else ""
            return Parsed(float(rank), f"{label}{suffix}")
    if "poe" in low:
        return Parsed(1.0, "PoE（页面未标协议等级）")
    return Parsed(None, text)


def p_codec(text: str) -> Parsed:
    codecs = set()
    for token in re.findall(r"H\.?26[45]", text, re.I):
        codecs.add("H.265" if token[-1] == "5" else "H.264")
    if re.search(r"MJPEG", text, re.I):
        codecs.add("MJPEG")
    ordered = tuple(sorted(codecs))
    return Parsed(None, "、".join(ordered) or text, tokens=ordered)


def p_ports(text: str) -> Parsed:
    count = _num(r"(\d+)\s*个", text, int)
    if re.search(r"1000|千兆", text):
        speed = 1000
    elif re.search(r"10\s*[M兆]/100", text):
        speed = 100
    else:
        speed = None
    label = {1000: "千兆自适应", 100: "百兆自适应"}.get(speed, "速率未标注")
    if count is None:
        return Parsed(None, text)
    return Parsed(float(count), f"{count} 个 RJ45（{label}）", tokens=(speed or 0,))


def p_speed(text: str) -> Parsed:
    if re.search(r"1000|千兆", text):
        return Parsed(1000.0, "千兆")
    if re.search(r"10\s*[M兆]/100", text) or "百兆" in text:
        return Parsed(100.0, "百兆")
    return Parsed(None, text)


def p_lanes(text: str) -> Parsed:
    v = _num(r"(\d+)\s*路", text, int)
    return Parsed(float(v), f"{v} 路") if v else Parsed(None, text)


def p_mbps(text: str) -> Parsed:
    vals = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*Mbps", text, re.I)]
    if not vals:
        return Parsed(None, text)
    distinct = sorted(set(vals), reverse=True)
    note = ""
    if len(distinct) > 1:
        note = f"原文另列 {'/'.join(f'{v:g}' for v in distinct[1:])} Mbps（不同智能配置口径），对比前需按实际配置核对"
    return Parsed(max(vals), f"{max(vals):g} Mbps", note, tokens=tuple(distinct))


def p_sata(text: str) -> Parsed:
    v = _num(r"(\d+)\s*个?\s*SATA", text, int)
    return Parsed(float(v), f"{v} 个 SATA") if v else Parsed(None, text)


def p_tb(text: str) -> Parsed:
    v = _num(r"(\d+(?:\.\d+)?)\s*T(?:B)?", text)
    return Parsed(v, f"{v:g} TB") if v is not None else Parsed(None, text)


def p_1080p(text: str) -> Parsed:
    vals = sorted({int(x) for x in re.findall(r"(\d+)\s*(?:路|×)\s*1080p", text, re.I)}, reverse=True)
    if not vals:
        return Parsed(None, text)
    note = ""
    if len(vals) > 1:
        note = f"原文另列 {'/'.join(str(v) for v in vals[1:])} 路 1080P 口径，取较大值"
    return Parsed(float(vals[0]), f"{vals[0]} 路 1080P", note, tokens=tuple(vals))


def p_outputs(text: str) -> Parsed:
    pairs = re.findall(r"(\d+)\s*路\s*(HDMI|VGA)", text, re.I)
    if not pairs:
        return Parsed(None, text)
    counts: dict[str, int] = {}
    for n, kind in pairs:
        kind = kind.upper()
        counts[kind] = max(counts.get(kind, 0), int(n))
    total = sum(counts.values())
    detail = " + ".join(f"{k} {v}" for k, v in counts.items())
    return Parsed(float(total), f"{total} 路输出（{detail}）")


def p_gbps(text: str) -> Parsed:
    v = _num(r"(\d+(?:\.\d+)?)\s*Gbps", text)
    return Parsed(v, f"{v:g} Gbps") if v is not None else Parsed(None, text)


def p_mpps(text: str) -> Parsed:
    v = _num(r"(\d+(?:\.\d+)?)\s*Mpps", text)
    return Parsed(v, f"{v:g} Mpps") if v is not None else Parsed(None, text)


def p_watt(text: str) -> Parsed:
    vals = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*(?:W|瓦)", text, re.I)]
    if not vals:
        return Parsed(None, text)
    v = max(vals)
    distinct = sorted(set(vals), reverse=True)
    note = ""
    if len(distinct) > 1:
        note = "原文列出多个功率口径（" + "/".join(f"{x:g}" for x in distinct) + " W），取最大值"
    return Parsed(v, f"{v:g} W", note)


def p_poe_ports(text: str) -> Parsed:
    # 补采后官网原文有 "8个千兆PoE电口" / "端口1~24" 等多种写法
    m = re.search(r"端口\s*(\d+)\s*[~～\-—]\s*(\d+)", text)
    if m:
        n = int(m.group(2))
        return Parsed(float(n), f"{n} 个 PoE 端口（端口 {m.group(1)}~{m.group(2)}）")
    v = _num(r"(\d+)\s*个", text, int)
    return Parsed(float(v), f"{v} 个 PoE 电口") if v else Parsed(None, text)


def p_fiber_ports(text: str) -> Parsed:
    # "8个10/100/1000Base-T端口，2个1000Base-X以太网端口" 要取的是光口数
    m = re.search(r"(\d+)\s*个[^,，]{0,24}?(?:光口|Base-X|SFP|光纤)", text, re.I)
    if m:
        return Parsed(float(m.group(1)), f"{m.group(1)} 个光口")
    m = re.search(r"(\d+)\s*个[^0-9]*光口", text)
    if m:
        return Parsed(float(m.group(1)), f"{m.group(1)} 个光口")
    v = _num(r"(\d+)\s*个", text, int)
    return Parsed(float(v), f"{v} 个光口") if v else Parsed(None, text)


def p_managed(text: str) -> Parsed:
    clean = text.strip()
    if re.search(r"非网管|不支持|无管理|非管理|不提供管理", clean):
        return Parsed(0.0, "非网管")
    if clean in {"是", "支持", "网管型", "管理型"}:
        return Parsed(1.0, "管理型")
    # 官网多写成 "Web，4200客户端，海康互联App" / "支持WEB管理" / "支持SNMP"
    if re.search(r"web|snmp|ssh|管理|网管|客户端|app|vlan|qos|远程配置", clean, re.I):
        return Parsed(1.0, "管理型（支持网管功能）")
    return Parsed(None, text)


FIELDS: dict[str, list[dict]] = {
    # hk 别名来自补采后 332 款海康机型在库里的真实 field_label 统计，
    # 按「精确字段名 → 常见同义字段」排序，_pick 会取第一个命中的。
    "网络摄像机": [
        dict(field="像素/分辨率", dh=["像素"],
             hk=["最大分辨率", "主码流帧率分辨率", "子码流帧率分辨率"], kind="num_tol",
             parse=p_pixel, unit="万像素", tol=0.1),
        dict(field="传感器尺寸", dh=["传感器类型"], hk=["传感器类型"], kind="num",
             parse=p_sensor, unit="英寸"),
        dict(field="宽动态", dh=["宽动态"], hk=["宽动态"], kind="num", parse=p_db, unit="dB"),
        dict(field="补光距离", dh=["最大补光距离", "补光距离"],
             hk=["最大补光距离", "补光距离", "补光灯距离"],
             kind="num", parse=p_distance, unit="m"),
        dict(field="视频压缩标准", dh=["视频压缩标准"], hk=["视频压缩标准", "编码类型"],
             kind="set", parse=p_codec),
        dict(field="供电/PoE 标准", dh=["工作电压", "供电方式"], hk=["供电方式"],
             kind="rank", parse=p_poe),
        dict(field="防护等级", dh=["防护等级"], hk=["防护", "物理防护", "电气安全防护"],
             kind="num", parse=p_ip, unit="IP"),
        dict(field="网络接口", dh=["网络接口"], hk=["网络"], kind="port", parse=p_ports),
    ],
    "NVR": [
        dict(field="接入路数", dh=["接入路数"], hk=["视频接入路数"], kind="num",
             parse=p_lanes, unit="路"),
        dict(field="网络输入带宽", dh=["网络带宽", "网络输入带宽"], hk=["网络输入带宽"],
             kind="num", parse=p_mbps, unit="Mbps"),
        dict(field="盘位", dh=["硬盘接口", "盘位"], hk=["盘位", "硬盘接口"], kind="num",
             parse=p_sata, unit="个"),
        dict(field="单盘容量", dh=["硬盘接口", "单盘容量"], hk=["单盘容量"], kind="num",
             parse=p_tb, unit="TB"),
        dict(field="解码能力", dh=["解码能力"], hk=["解码能力"], kind="num",
             parse=p_1080p, unit="路 1080P"),
        dict(field="视频输出", dh=["视频输出"], hk=["视频输出"], kind="num",
             parse=p_outputs, unit="路"),
        dict(field="视频压缩标准", dh=["视频压缩标准"], hk=["视频解码格式"], kind="set",
             parse=p_codec),
    ],
    "PoE交换机": [
        dict(field="PoE 端口数", dh=["PoE端口数", "接入电口"],
             hk=["PoE端口数", "接入电口", "端口规格", "PoE端口", "端口", "端口配置", "业务端口"],
             kind="num", parse=p_poe_ports, unit="个"),
        dict(field="上行光口", dh=["上行光口"],
             hk=["上行光口", "端口规格", "端口"], kind="num",
             parse=p_fiber_ports, unit="个"),
        dict(field="电口速率", dh=["电口速率"],
             hk=["端口规格", "端口", "最大空口传输速率"], kind="num",
             parse=p_speed, unit="Mbps"),
        dict(field="管理型", dh=["是否管理型"],
             hk=["是否管理型", "管理方式", "管理维护", "Web管理", "web管理",
                 "管理规格", "统一管理平台"], kind="bool",
             parse=p_managed),
        dict(field="交换容量", dh=["交换容量"], hk=["交换容量"], kind="num",
             parse=p_gbps, unit="Gbps"),
        dict(field="包转发率", dh=["包转发率"], hk=["包转发率"], kind="num",
             parse=p_mpps, unit="Mpps"),
        dict(field="PoE 总功率", dh=["PoE功率", "整机最大供电功率"],
             hk=["整机最大供电功率", "PoE功率", "POE输出功率", "端口最大供电功率", "PoE标准"],
             kind="num", parse=p_watt, unit="W"),
    ],
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load(conn: sqlite3.Connection, category: str):
    dh, hk = REPRESENTATIVES.get(category, (None, None))
    rows = conn.execute(
        "SELECT brand, model, display_name, section, field_label, raw_value, "
        "source_url, evidence, retrieved_at FROM competitor_facts "
        "WHERE category=? AND ((brand=? AND model=?) OR (brand=? AND model=?)) "
        "ORDER BY id", (category, DAHUA, dh, HIK, hk),
    ).fetchall()
    if not rows:
        raise ValueError(f"竞品事实库为空（{category}），请先运行 python -m scraper.collect_competitor")

    facts: dict[str, dict] = {DAHUA: {}, HIK: {}}
    meta: dict[str, dict] = {}
    for r in rows:
        brand = r["brand"]
        if brand not in facts:
            continue
        facts[brand][r["field_label"]] = dict(r)
        meta[brand] = {
            "model": r["model"], "display_name": r["display_name"],
            "source_url": r["source_url"], "retrieved_at": r["retrieved_at"],
        }
    return facts, meta


def _pick(facts: dict, labels: list[str]) -> dict | None:
    for label in labels:
        if label in facts:
            return facts[label]
    return None


def _delta(a: float, b: float, unit: str) -> str:
    diff = a - b
    sign = "+" if diff > 0 else ""
    text = f"差 {sign}{diff:g} {unit}".strip()
    if b:
        pct = diff / abs(b) * 100
        text += f"（{sign}{pct:.0f}%）" if diff else "（持平）"
    return text


def _verdict(field: dict, a: Parsed | None, b: Parsed | None) -> tuple[str, str, str]:
    """返回 (状态, 差异说明, 口径备注)。"""
    if a is None:
        return "only_hik", "", "大华官网未标注该字段"
    if b is None:
        return "only_dahua", "", "海康官网未标注该字段"

    kind, unit = field["kind"], field.get("unit", "")

    if kind in ("num", "num_tol", "rank", "bool"):
        if a.num is None or b.num is None:
            who = "大华" if a.num is None else "海康"
            return "qualitative", "", f"{who}原文未给出可换算数值，口径不可直接比较"
        diff = a.num - b.num
        if kind == "num_tol" and b.num and abs(diff) / abs(b.num) <= field.get("tol", 0.1):
            return "same", "", f"差异在 {field['tol'] * 100:.0f}% 容差内，视为同档"
        if abs(diff) < 1e-9:
            return "same", "", a.note or b.note
        if a.tokens and len(a.tokens) > 1:
            calibers = "、".join(f"{v:g}" for v in a.tokens)
            return "qualitative", "", \
                f"大华原文给出多个口径（{calibers}），海康为 {b.num:g}；不同配置下结论不同，不作单一数值断言"
        winner = "dahua" if diff > 0 else "hik"
        delta = _delta(a.num, b.num, unit) if kind != "rank" else f"高 {abs(diff):g} 个协议档位"
        return winner, delta, "；".join(x for x in (a.note, b.note) if x)

    if kind == "set":
        sa, sb = set(a.tokens), set(b.tokens)
        if not sa or not sb:
            return "qualitative", "", "官网未列出可对齐的编码格式"
        if sa == sb:
            return "same", "", ""
        if sa > sb:
            return "dahua", f"多 {('、'.join(sorted(sa - sb)))}", ""
        if sb > sa:
            return "hik", f"多 {('、'.join(sorted(sb - sa)))}", ""
        return "qualitative", "", "双方编码格式集合不同，非同一口径"

    if kind == "port":
        if a.num is None or b.num is None:
            return "qualitative", "", "官网未给出可对齐的端口数量/速率"
        key_a, key_b = (a.tokens and a.tokens[0], a.num), (b.tokens and b.tokens[0], b.num)
        if key_a == key_b:
            return "same", "", ""
        winner = "dahua" if key_a > key_b else "hik"
        return winner, f"大华 {a.display} / 海康 {b.display}", ""

    return "qualitative", "", "字段口径不一致"


def _talking(name: str, status: str, a: Parsed, b: Parsed, delta: str, note: str) -> str:
    dh, hk = a.display or "—", b.display or "—"
    if status == "same":
        same_text = f"（{dh}）" if dh == hk else f"（大华 {dh} / 海康 {hk}，折算后同档）"
        text = f"同档：{name}双方一致{same_text}，单点参数不构成差异，建议把话术放在系统兼容性与整体交付上。"
    elif status == "dahua":
        text = f"我方占优：{name} 大华 {dh}，海康 {hk}" + (f"，{delta}" if delta else "") + \
               "；可直接引用官网参数页作为对比依据。"
    elif status == "hik":
        text = f"对方占优：{name} 海康 {hk}，大华 {dh}" + (f"，{delta}" if delta else "") + \
               "；建议不做硬参数对比，转讲方案兼容性、供货与售后响应。"
    elif status == "qualitative":
        text = f"口径不同：{name} 大华「{dh}」/ 海康「{hk}」，官网口径不可直接换算，仅作定性参考、不作数值断言。"
    elif status == "only_dahua":
        text = f"仅大华公开：{name} 大华标注「{dh}」；海康官网未标注该项，不做对比断言，需要时向对方索取资料。"
    else:
        text = f"仅海康公开：{name} = {hk}；大华官网未标注该项，不作断言。"
    if note:
        text += f"（口径备注：{note}）"
    return text


def compare(category: str) -> dict:
    conn = _connect()
    try:
        facts, meta = _load(conn, category)
    finally:
        conn.close()

    fields = FIELDS[category]
    rows = []
    summary = {k: 0 for k in STATUS_LABEL}
    for spec in fields:
        d = _pick(facts[DAHUA], spec["dh"])
        h = _pick(facts[HIK], spec["hk"])
        a = spec["parse"](d["raw_value"]) if d else None
        b = spec["parse"](h["raw_value"]) if h else None
        status, delta, note = _verdict(spec, a, b)
        summary[status] += 1

        dh_disp = (a.display or d["raw_value"]) if (a and d) else "官网未公开"
        hk_disp = (b.display or h["raw_value"]) if (b and h) else "官网未公开"
        talk = _talking(spec["field"], status, a or Parsed(None, dh_disp),
                        b or Parsed(None, hk_disp), delta, note)
        density = annotate(talk)

        rows.append({
            "field": spec["field"],
            "status": status,
            "status_label": STATUS_LABEL[status],
            "dahua_value": dh_disp,
            "hik_value": hk_disp,
            "dahua_raw": d["raw_value"] if d else "",
            "hik_raw": h["raw_value"] if h else "",
            "dahua_section": d["section"] if d else "",
            "hik_section": h["section"] if h else "",
            "dahua_source": d["source_url"] if d else "",
            "hik_source": h["source_url"] if h else "",
            "dahua_evidence": d["evidence"] if d else "",
            "hik_evidence": h["evidence"] if h else "",
            "delta": delta,
            "note": note,
            "talking_point": talk,
            "density_score": density["density_score"],
            "density_kind": density["page_kind"],
        })

    retrieved = max(m["retrieved_at"] for m in meta.values()) if meta else ""
    return {
        "category": category,
        "dahua_model": meta.get(DAHUA, {}).get("model", ""),
        "hik_model": meta.get(HIK, {}).get("model", ""),
        "dahua_name": meta.get(DAHUA, {}).get("display_name", ""),
        "hik_name": meta.get(HIK, {}).get("display_name", ""),
        "dahua_source": meta.get(DAHUA, {}).get("source_url", ""),
        "hik_source": meta.get(HIK, {}).get("source_url", ""),
        "retrieved_at": retrieved,
        "rows": rows,
        "summary": summary,
        "disclaimer": "对比机型为双方官网同档位代表款（非渠道订货 SKU）；参数均取自官网公开参数页，"
                      "缺失项不作推断；海康官网无公开报价，价格不纳入对比。",
    }


def _clip(text: str, limit: int = 160) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def export_docx(result: dict, out_path: Path | str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    from docx import Document
    from docx.shared import Pt

    doc = Document()
    section = doc.sections[0]
    section.left_margin = section.right_margin = Cm(2)
    section.top_margin = section.bottom_margin = Cm(1.8)

    _para(doc, f"竞品对比 · {result['category']}", 20, True, ACCENT, WD_ALIGN_PARAGRAPH.CENTER, 2)
    _para(doc, "大华 vs 海康 · 逐字段对照（官网公开参数）", 12, False, MUTED, WD_ALIGN_PARAGRAPH.CENTER, 6)
    _para(doc, f"大华 {result['dahua_model']}　|　海康 {result['hik_model']}　|　"
               f"数据抓取：{result['retrieved_at']}", 9, False, MUTED, WD_ALIGN_PARAGRAPH.CENTER, 10)

    s = result["summary"]
    _heading(doc, "一、结论速览")
    _para(doc, f"共 {len(result['rows'])} 个可比字段：同档 {s['same']} 项、大华占优 {s['dahua']} 项、"
               f"海康占优 {s['hik']} 项、口径不同 {s['qualitative']} 项、"
               f"仅一方公开 {s['only_dahua'] + s['only_hik']} 项。", 10.5, space_after=6)
    _para(doc, result["disclaimer"], 9, False, MUTED, space_after=10)

    _heading(doc, "二、逐字段对照表")
    _table(
        doc,
        ["字段", "大华（归一化）", "海康（归一化）", "判定", "差异 / 口径说明"],
        [[r["field"], r["dahua_value"], r["hik_value"], r["status_label"],
          "；".join(x for x in (r["delta"], r["note"]) if x)] for r in result["rows"]],
        [2.6, 4.2, 4.2, 2.0, 4.0],
    )

    _heading(doc, "三、销售话术（技术密度标注）")
    _table(
        doc,
        ["字段", "话术", "技术密度"],
        [[r["field"], r["talking_point"],
          f"{r['density_score']:.2f} · {r['density_kind']}"] for r in result["rows"]],
        [2.8, 11.4, 2.8],
    )
    _para(doc, "密度 = 技术词命中 /（技术词 + 营销词），≥0.6 判为 tech_page；"
               "低密度话术仅作措辞参考，数值结论以对照表与官网原文为准。", 8.5, False, MUTED, space_after=10)

    _heading(doc, "四、原文依据（逐条可回溯）")
    _para(doc, f"大华：{result['dahua_source']}", 9, False, MUTED, space_after=2)
    _para(doc, f"海康：{result['hik_source']}", 9, False, MUTED, space_after=6)
    _table(
        doc,
        ["字段", "大华官网原文", "海康官网原文"],
        [[r["field"], _clip(r["dahua_raw"]) or "—", _clip(r["hik_raw"]) or "—"]
         for r in result["rows"]],
        [2.6, 7.2, 7.2],
    )
    _para(doc, "原文为官网参数页字段值，长文本已截断；完整证据见本地库 competitor_facts.evidence。",
          8.5, False, MUTED, space_after=6)

    _para(doc, "免责声明：本表由程序从双方官网公开参数页生成，仅作售前参考；"
               "参数口径以官网当期页面为准，交付前须人工复核。",
          8.5, False, RGBColor(0x6B, 0x7A, 0x8A), space_after=0)

    doc.save(out)
    return out


def _main() -> None:
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="大华 vs 海康 逐字段对比")
    ap.add_argument("--category", default="网络摄像机", choices=list(FIELDS))
    ap.add_argument("--docx", default="", help="导出 DOCX 路径（可选）")
    args = ap.parse_args()

    result = compare(args.category)
    print(f"\n{result['category']}：大华 {result['dahua_model']} vs 海康 {result['hik_model']}")
    print("-" * 100)
    for r in result["rows"]:
        print(f"[{r['status_label']:<6}] {r['field']:<10} 大华: {r['dahua_value'][:36]:<38} "
              f"海康: {r['hik_value'][:36]}")
        print(f"          话术: {r['talking_point']}")
    print("-" * 100)
    print("统计：", result["summary"])
    if args.docx:
        print("DOCX:", export_docx(result, args.docx))


if __name__ == "__main__":
    _main()
