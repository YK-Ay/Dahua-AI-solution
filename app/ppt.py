"""PPT 版方案：把 Spec 渲染成 16:9 售前汇报演示文稿。

设计原则（对齐优秀投标/汇报 PPT 的常见做法）：
  · 一页一个观点，标题短、层级清楚，避免整页文字
  · 数字前置：封面与摘要先给点位、存储、设备量、预算
  · 统一栅格与页脚，红白主色，弱化装饰、强化对比
  · 内容全部来自确定性 Spec 与 quote，不额外编造
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.models import Spec
from app.quote import build_quote

ACCENT = RGBColor(0xC8, 0x10, 0x2E)
ACCENT_DARK = RGBColor(0x8E, 0x0B, 0x20)
INK = RGBColor(0x1A, 0x1D, 0x24)
GRAY = RGBColor(0x6B, 0x72, 0x80)
MUTED = RGBColor(0x9A, 0xA1, 0xAC)
LINE = RGBColor(0xE5, 0xE7, 0xEB)
LIGHT = RGBColor(0xF6, 0xF7, 0xF9)
SOFT_RED = RGBColor(0xFD, 0xEC, 0xEE)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "微软雅黑"

W, H = Inches(13.333), Inches(7.5)
PAGE_W = 13.333
MARGIN = 0.75
CONTENT_W = PAGE_W - MARGIN * 2


def _shape(slide, left, top, width, height, fill=WHITE, line=None, radius=None):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius is not None else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(kind, Inches(left), Inches(top), Inches(width), Inches(height))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(0.75)
    shp.shadow.inherit = False
    if radius is not None:
        try:
            shp.adjustments[0] = radius
        except (IndexError, ValueError):
            pass
    return shp


def _text(slide, left, top, width, height, text, size=14, bold=False, color=INK,
          align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = spacing
    run = p.add_run()
    run.text = text
    _font(run, size, bold, color)
    return box


def _font(run, size, bold=False, color=INK):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = FONT
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", FONT)


def _bullets(slide, left, top, width, height, lines, size=12.5, color=INK,
             bullet="•", spacing=1.35):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = 0
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = spacing
        p.space_after = Pt(4)
        run = p.add_run()
        run.text = f"{bullet} {line}" if bullet else str(line)
        _font(run, size, False, color)
    return box


def _clip(text: str, size: float, width_in: float) -> str:
    """按字号与可用宽度截断（中文 1em、西文 0.55em），避免文本溢出卡片。"""
    text = str(text or "")
    limit = max(2.0, width_in * 72 / size)
    used, out = 0.0, ""
    for ch in text:
        w = 0.55 if ord(ch) < 128 else 1.0
        if used + w > limit:
            return out + "…"
        used += w
        out += ch
    return out


def _bar(slide, left, top, width, height, color=ACCENT):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top),
                                 Inches(width), Inches(height))
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    shp.line.fill.background()
    shp.shadow.inherit = False


def _line(slide, left, top, width, color=LINE, weight=0.75):
    shp = slide.shapes.add_connector(1, Inches(left), Inches(top),
                                     Inches(left + width), Inches(top))
    shp.line.color.rgb = color
    shp.line.width = Pt(weight)


def _header(slide, no: str, title: str, kicker: str = ""):
    _text(slide, MARGIN, 0.46, 0.8, 0.3, no, 10.5, True, ACCENT)
    _text(slide, MARGIN, 0.7, 11, 0.55, title, 23, True, INK)
    if kicker:
        _text(slide, MARGIN, 1.24, 11.5, 0.3, kicker, 10.5, False, GRAY)
    _line(slide, MARGIN, 1.62, CONTENT_W)


def _footer(slide, page: int):
    _line(slide, MARGIN, 7.02, CONTENT_W, LINE)
    _text(slide, MARGIN, 7.1, 7, 0.24, "大华解决方案助手 · 方案建议书", 8.5, False, MUTED)
    _text(slide, PAGE_W - MARGIN - 1, 7.1, 1, 0.24, f"{page:02d}", 8.5, False, MUTED,
          PP_ALIGN.RIGHT)


def _kpi(slide, left, top, width, height, value, label, accent=ACCENT):
    _shape(slide, left, top, width, height, LIGHT, None, radius=0.08)
    _bar(slide, left, top, 0.055, height, accent)
    _text(slide, left + 0.24, top + 0.18, width - 0.36, 0.42, value, 22, True, accent)
    _text(slide, left + 0.24, top + 0.64, width - 0.36, 0.26, label, 9.5, False, GRAY)


def _plain_table(slide, left, top, width, headers, rows, ratios=None, size=10,
                 row_h=0.34, total_row=False):
    shape = slide.shapes.add_table(len(rows) + 1, len(headers), Inches(left), Inches(top),
                                   Inches(width), Inches(row_h * (len(rows) + 1)))
    table = shape.table
    table.first_row = False
    table.horz_banding = False
    tblPr = table._tbl.tblPr
    for el in tblPr.findall(qn("a:tableStyleId")):
        tblPr.remove(el)
    if ratios:
        total = sum(ratios)
        for i, ratio in enumerate(ratios):
            table.columns[i].width = int(Inches(width) * ratio / total)
    for i in range(len(rows) + 1):
        table.rows[i].height = Inches(row_h)
    fills = {0: LIGHT}
    for r in range(1, len(rows) + 2):
        fills[r] = WHITE if r % 2 else RGBColor(0xFB, 0xFB, 0xFC)
    if total_row:
        fills[len(rows)] = SOFT_RED
    for r in range(len(rows) + 1):
        for c in range(len(headers)):
            cell = table.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = fills.get(r, WHITE)
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.06)
            cell.margin_top = Inches(0.02)
            cell.margin_bottom = Inches(0.02)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            if r == 0:
                run.text = headers[c]
                _font(run, size, True, GRAY)
            elif total_row and r == len(rows):
                run.text = str(rows[r - 1][c])
                _font(run, size, True, ACCENT_DARK)
            else:
                run.text = str(rows[r - 1][c])
                _font(run, size, False, INK)
    return table


def _lanes(spec: Spec) -> int:
    return sum(int(r.lanes or 0) for r in spec.requirements)


# 五层架构：名称、匹配规则、代表色。架构页与拓扑页共用，保证两页口径一致。
_LAYERS = [
    ("前端采集", lambda c: "摄像" in c or "球机" in c or "枪机" in c, ACCENT),
    ("网络传输", lambda c: "交换机" in c, RGBColor(0xD9, 0x77, 0x06)),
    ("存储录像", lambda c: "NVR" in c or "硬盘" in c, RGBColor(0x25, 0x63, 0xEB)),
    ("平台管理", lambda c: "平台" in c or "服务器" in c, RGBColor(0x05, 0x96, 0x69)),
    ("展示上墙", lambda c: "监视器" in c or "拼接" in c or "显示" in c or "解码" in c,
     RGBColor(0x7C, 0x3A, 0xED)),
]


def _layer_stats(spec: Spec, match) -> tuple[int, list[tuple[str, int]], int]:
    """返回（设备总数、按类别汇总的数量、机型数）。"""
    hits = [d for d in spec.devices if match(d.category)]
    agg: dict[str, int] = {}
    models: set[str] = set()
    for d in hits:
        agg[d.category] = agg.get(d.category, 0) + int(d.qty or 0)
        if d.model:
            models.add(d.model)
    return sum(agg.values()), list(agg.items()), len(models)


def _qty(spec: Spec) -> int:
    return sum(int(d.qty or 0) for d in spec.devices)


def _money(value: float) -> str:
    return f"¥{value:,.0f}"


# ---------------------------------------------------------------------------
# 各页
# ---------------------------------------------------------------------------

def _cover(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _shape(slide, 0, 0, 4.55, 7.5, ACCENT)
    _shape(slide, 0, 0, 4.55, 0.14, ACCENT_DARK)
    _text(slide, 0.62, 0.7, 3.3, 0.4, "DH", 26, True, WHITE)
    _text(slide, 0.62, 1.12, 3.3, 0.3, "DAHUA SOLUTION DESK", 9, False,
          RGBColor(0xFF, 0xD9, 0xDE))
    _text(slide, 0.62, 4.35, 3.5, 0.3, "SOLUTION PROPOSAL", 9.5, False,
          RGBColor(0xFF, 0xD9, 0xDE))
    _text(slide, 0.62, 4.7, 3.5, 1.4, spec.project.name, 24, True, WHITE, spacing=1.25)
    _line(slide, 0.62, 6.55, 0.9, WHITE, 1.5)
    _text(slide, 0.62, 6.72, 3.4, 0.3, "安防系统解决方案", 11, False,
          RGBColor(0xFF, 0xD9, 0xDE))

    _text(slide, 5.35, 1.15, 7.2, 0.3, "PROJECT BRIEF", 10, True, ACCENT)
    _text(slide, 5.35, 1.5, 7.4, 1.5,
          "面向实际场景的\n安防系统设计建议", 31, True, INK, spacing=1.3)
    _text(slide, 5.35, 3.35, 7.2, 0.4,
          f"{spec.project.industry or '行业'} · {len(spec.requirements)} 类场景 · "
          f"{_lanes(spec)} 路点位", 13, False, GRAY)

    quote = build_quote(spec)
    chips = [
        (f"{_lanes(spec)} 路", "视频点位"),
        (f"{spec.storage.retention_days} 天", "存储周期"),
        (f"{_qty(spec)} 台", "设备数量"),
        (_money(quote["total"]), "标准价合计"),
    ]
    cw, gap = 1.72, 0.16
    for i, (value, label) in enumerate(chips):
        x = 5.35 + i * (cw + gap)
        _shape(slide, x, 4.25, cw, 1.12, LIGHT, None, radius=0.1)
        _text(slide, x + 0.16, 4.42, cw - 0.28, 0.36, value, 15, True, ACCENT_DARK)
        _text(slide, x + 0.16, 4.84, cw - 0.28, 0.26, label, 8.5, False, GRAY)

    _line(slide, 5.35, 5.95, 7.0, LINE)
    meta = [
        ("行业", spec.project.industry or "—"),
        ("编制日期", date.today().isoformat()),
        ("编制", "大华解决方案助手（草稿）"),
    ]
    x = 5.35
    for label, value in meta:
        _text(slide, x, 6.12, 2.2, 0.24, label, 8.5, False, MUTED)
        _text(slide, x, 6.36, 2.2, 0.28, value, 10, False, INK)
        x += 2.35


def _summary(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, "01", "方案摘要", "先看结论：规模、存储、设备量与预算一次对齐")
    quote = build_quote(spec)
    lanes = _lanes(spec)
    storage = spec.storage
    items = [
        (f"{lanes}", "视频点位（路）"),
        (f"{storage.retention_days}", "存储周期（天）"),
        (f"{_qty(spec)}", "设备数量（台）"),
        (_money(quote["total"]), "标准价合计"),
    ]
    cw, gap = 2.72, 0.2
    for i, (value, label) in enumerate(items):
        _kpi(slide, MARGIN + i * (cw + gap), 1.95, cw, 1.12, value, label)

    _text(slide, MARGIN, 3.45, 6, 0.34, "设计要点", 14, True, INK)
    points = [
        ("场景化选型", f"{len(spec.requirements)} 类场景分别匹配机型，"
                    f"共 {len(spec.devices)} 类设备，选型依据随方案输出。"),
        ("确定性测算", f"存储 {storage.with_redundancy_tb} TB（含冗余），"
                    f"上行 {spec.network.uplink_mbps} Mbps，全部由本地规则核算。"),
        ("可审阅交付", "Word / PPT / 拓扑图同源生成，数字与型号可追溯到产品库。"),
    ]
    y = 3.95
    for i, (title, body) in enumerate(points):
        _shape(slide, MARGIN, y, CONTENT_W, 0.86, LIGHT, None, radius=0.06)
        _text(slide, MARGIN + 0.28, y + 0.13, 0.5, 0.24, f"0{i + 1}", 10, True, ACCENT)
        _text(slide, MARGIN + 0.95, y + 0.13, 2.0, 0.28, title, 12, True, INK)
        _text(slide, MARGIN + 3.1, y + 0.13, 8.2, 0.6, body, 10.5, False, GRAY, spacing=1.35)
        y += 1.0


def _demand(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, "02", "需求理解", "按使用场景拆分点位，明确每类区域的建设目标")
    _text(slide, MARGIN, 1.9, 3.4, 0.26, "总点位", 9.5, False, GRAY)
    _text(slide, MARGIN, 2.18, 3.4, 0.9, f"{_lanes(spec)}", 52, True, ACCENT)
    _text(slide, MARGIN + 1.55, 2.62, 2.2, 0.3, "路", 12, False, GRAY)
    _line(slide, MARGIN, 3.32, 3.3, LINE)
    stats = [
        ("场景类别", f"{len(spec.requirements)} 类"),
        ("存储周期", f"{spec.storage.retention_days} 天"),
        ("冗余后容量", f"{spec.storage.with_redundancy_tb} TB"),
        ("上行带宽", f"{spec.network.uplink_mbps} Mbps"),
    ]
    y = 3.55
    for label, value in stats:
        _text(slide, MARGIN, y, 1.6, 0.3, label, 10, False, GRAY)
        _text(slide, MARGIN + 1.65, y - 0.03, 1.7, 0.32, value, 12, True, INK)
        y += 0.6

    rows = [[r.target, r.intent or "—", r.lanes, f"{r.bitrate_mbps:g}"]
            for r in spec.requirements]
    row_h = min(0.66, max(0.42, 4.4 / (len(rows) + 1)))
    _plain_table(slide, 4.55, 1.95, CONTENT_W - 3.8,
                 ["使用场景", "建设目标", "点位数", "码流 Mbps"], rows,
                 [2.6, 4.4, 1.2, 1.6], size=10.5, row_h=row_h)


def _architecture(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, "03", "总体架构", "前端采集 → 网络传输 → 存储录像 → 平台管理 → 展示上墙")

    cw, gap = 2.14, 0.17
    for i, (name, match, color) in enumerate(_LAYERS):
        x = MARGIN + i * (cw + gap)
        _shape(slide, x, 1.95, cw, 3.72, WHITE, LINE, radius=0.06)
        _bar(slide, x, 1.95, cw, 0.07, color)
        _text(slide, x + 0.22, 2.16, 0.6, 0.24, f"0{i + 1}", 9.5, True, color)
        _text(slide, x + 0.22, 2.4, cw - 0.4, 0.32, name, 13, True, INK)

        total, cats, model_n = _layer_stats(spec, match)
        _text(slide, x + 0.22, 2.9, cw - 0.4, 0.5, f"{total} 台", 21, True, color)
        _text(slide, x + 0.22, 3.42, cw - 0.4, 0.24,
              f"{model_n} 款机型 · {len(cats)} 类", 8.5, False, MUTED)
        lines = [f"{cat} ×{qty}" for cat, qty in cats[:3]]
        _bullets(slide, x + 0.22, 3.85, cw - 0.4, 1.6, lines or ["—"],
                 size=9.5, color=GRAY, bullet="·", spacing=1.5)
        if len(cats) > 3:
            _text(slide, x + 0.22, 5.28, cw - 0.4, 0.24,
                  f"另 {len(cats) - 3} 类设备", 8.5, False, MUTED)


# 与前端拓扑一致的设备类别配色，保证 PPT 与页面观感统一
_CAT_COLORS = [
    ("摄像", RGBColor(0x00, 0x91, 0xBE)),
    ("交换机", RGBColor(0x16, 0x96, 0x6E)),
    ("NVR", RGBColor(0xC8, 0x78, 0x00)),
    ("录像机", RGBColor(0xC8, 0x78, 0x00)),
    ("硬盘", RGBColor(0x78, 0x50, 0xBE)),
    ("监视器", RGBColor(0xD6, 0x33, 0x6C)),
    ("平台", ACCENT),
]


def _cat_color(category: str) -> RGBColor:
    for key, color in _CAT_COLORS:
        if key in category:
            return color
    return RGBColor(0x60, 0x6E, 0x82)


def _topology(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, "04", "系统拓扑", "前端采集 → 网络传输 → 集中存储 → 平台管理 → 显示上墙")

    label_w, band_h, band_gap, top0 = 1.76, 0.82, 0.17, 1.92
    area_x = MARGIN + label_w + 0.2
    area_w = CONTENT_W - label_w - 0.4

    for i, (name, match, color) in enumerate(_LAYERS):
        y = top0 + i * (band_h + band_gap)
        _shape(slide, MARGIN, y, CONTENT_W, band_h, LIGHT, None, radius=0.05)

        total, _, _ = _layer_stats(spec, match)
        _shape(slide, MARGIN, y, label_w, band_h, ACCENT, None, radius=0.05)
        _text(slide, MARGIN + 0.2, y + 0.14, label_w - 0.3, 0.28, name, 11.5, True, WHITE)
        _text(slide, MARGIN + 0.2, y + 0.44, label_w - 0.3, 0.24,
              f"{total} 台", 9, False, RGBColor(0xFF, 0xD9, 0xDE))

        # 每款机型一个信息胶囊：型号 + 数量/类别
        merged: dict[str, dict] = {}
        for d in spec.devices:
            if not match(d.category):
                continue
            key = d.model or d.category
            row = merged.setdefault(key, {"qty": 0, "cat": d.category})
            row["qty"] += int(d.qty or 0)
        chips = sorted(merged.items(), key=lambda kv: -kv[1]["qty"])[:5]
        n = max(1, len(chips))
        chip_w = min(2.62, (area_w - (n - 1) * 0.14) / n)
        for c in range(n):
            cx = area_x + c * (chip_w + 0.14)
            _shape(slide, cx, y + 0.12, chip_w, band_h - 0.24, WHITE, LINE, radius=0.08)
            if not chips:
                _text(slide, cx + 0.16, y + 0.26, chip_w - 0.3, 0.26,
                      "按需配置", 9, False, MUTED)
                continue
            model, info = chips[c]
            cat = info["cat"]
            accent = _cat_color(cat)
            _bar(slide, cx + 0.16, y + 0.24, 0.05, 0.34, accent)
            _text(slide, cx + 0.32, y + 0.2, chip_w - 0.48, 0.24,
                  _clip(model, 9, chip_w - 0.6), 9, True, INK)
            _text(slide, cx + 0.32, y + 0.44, chip_w - 0.48, 0.22,
                  _clip(f"×{info['qty']} · {cat}", 8, chip_w - 0.6), 8, False, GRAY)

        if i < len(_LAYERS) - 1:
            arrow = slide.shapes.add_shape(
                MSO_SHAPE.ISOSCELES_TRIANGLE,
                Inches(MARGIN + label_w / 2 - 0.12), Inches(y + band_h + 0.02),
                Inches(0.24), Inches(0.13))
            arrow.rotation = 180
            arrow.fill.solid()
            arrow.fill.fore_color.rgb = RGBColor(0xE7, 0xB2, 0xB8)
            arrow.line.fill.background()
            arrow.shadow.inherit = False


def _devices(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    quote = build_quote(spec)
    _header(slide, "05", "设备清单与报价",
            "标准价合计，未含税费、折扣与施工维保，最终以商务报价为准")
    rows = []
    for line in quote["lines"]:
        rows.append([line["index"], line["category"], line["model"], line["qty"],
                     "待询价" if line["unit_price"] is None else f"{line['unit_price']:,.0f}",
                     "—" if line["amount"] is None else f"{line['amount']:,.0f}"])
    rows.append(["", "", "合计", "", "", f"{quote['total']:,.0f}"])
    size = 9.5 if len(rows) <= 9 else 8.5
    row_h = min(0.44, max(0.3, 4.75 / (len(rows) + 1)))
    _plain_table(slide, MARGIN, 1.9, CONTENT_W,
                 ["序号", "类别", "型号", "数量", "单价（元）", "小计（元）"], rows,
                 [0.7, 2.2, 4.6, 1.1, 1.9, 2.0], size=size,
                 row_h=row_h, total_row=True)
    if quote["unpriced"]:
        _text(slide, MARGIN, 6.55, CONTENT_W, 0.3,
              f"{quote['unpriced']} 项无标准价（拼接单元等），标注「待询价」，未计入合计。",
              9, False, MUTED)


def _reasons(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, "06", "选型依据", "每类设备的选型理由可追溯到方案配单与产品库")
    rows = []
    for d in spec.devices[:7]:
        rows.append([d.category, d.model, (d.reason or "—")[:70]])
    _plain_table(slide, MARGIN, 1.9, CONTENT_W,
                 ["设备类别", "推荐型号", "选型依据"], rows,
                 [1.8, 3.4, 6.6], size=9.5, row_h=0.56)


def _boundaries(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, "07", "交付边界", "把范围、假设与待确认事项说清楚，减少后期返工")

    blocks = [
        ("包含范围", spec.boundaries.included, RGBColor(0x05, 0x96, 0x69)),
        ("不包含", spec.boundaries.excluded, RGBColor(0xD9, 0x77, 0x06)),
        ("关键假设", spec.boundaries.assumptions, RGBColor(0x25, 0x63, 0xEB)),
        ("待确认事项", spec.gaps, ACCENT),
    ]
    cw = (CONTENT_W - 0.24) / 2
    for i, (title, items, color) in enumerate(blocks):
        x = MARGIN + (i % 2) * (cw + 0.24)
        y = 1.92 + (i // 2) * 2.45
        _shape(slide, x, y, cw, 2.2, WHITE, LINE, radius=0.05)
        _bar(slide, x, y, 0.06, 2.2, color)
        _text(slide, x + 0.26, y + 0.14, cw - 0.4, 0.28, title, 12, True, color)
        if items:
            lines = [f"· {item}" for item in items][:5]
            _text(slide, x + 0.26, y + 0.54, cw - 0.46, 1.55,
                  "\n".join(lines), 9.5, False, INK, spacing=1.5)
        else:
            _text(slide, x + 0.26, y + 0.54, cw - 0.46, 0.3, "暂无", 9.5, False, MUTED)


def _next(prs, spec: Spec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _shape(slide, 0, 0, PAGE_W, 2.35, ACCENT)
    _text(slide, MARGIN, 0.62, 8, 0.3, "NEXT STEP", 10, True, RGBColor(0xFF, 0xD9, 0xDE))
    _text(slide, MARGIN, 0.95, 11.5, 0.7, "从方案到落地，三步推进", 30, True, WHITE)
    _text(slide, MARGIN, 1.75, 11, 0.3,
          f"{spec.project.name} · {spec.project.industry or '行业'} · "
          f"{_lanes(spec)} 路点位", 11, False, RGBColor(0xFF, 0xD9, 0xDE))

    steps = [
        ("确认清单", "核对场景、点位与存储周期，锁定设备型号与数量。"),
        ("现场勘察", "核实立杆、取电、链路与机柜条件，修正施工范围。"),
        ("商务报价", "按最终清单出具含税与施工的正式报价与交付计划。"),
    ]
    cw, gap = 3.6, 0.4
    for i, (title, body) in enumerate(steps):
        x = MARGIN + i * (cw + gap)
        _text(slide, x, 2.9, 0.6, 0.4, f"0{i + 1}", 14, True, ACCENT)
        _text(slide, x, 3.35, cw, 0.36, title, 15, True, INK)
        _text(slide, x, 3.85, cw - 0.2, 1.1, body, 10.5, False, GRAY, spacing=1.5)
        _line(slide, x, 4.98, cw - 0.4, LINE)

    _text(slide, MARGIN, 5.55, 11.8, 0.36,
          "本方案由大华解决方案助手生成，数字与选型均可复核；确认后可作为正式方案底稿。",
          10, False, MUTED)
    _text(slide, PAGE_W - MARGIN - 2.4, 6.35, 2.4, 0.5, "谢谢", 22, True, ACCENT,
          PP_ALIGN.RIGHT)


def render_pptx(spec: Spec, out_path: Path | str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H

    _cover(prs, spec)
    _summary(prs, spec)
    _demand(prs, spec)
    _architecture(prs, spec)
    _topology(prs, spec)
    _devices(prs, spec)
    _reasons(prs, spec)
    _boundaries(prs, spec)
    _next(prs, spec)

    for i in range(1, len(prs.slides)):
        _footer(prs.slides[i], i)
    prs.save(out)
    return out
