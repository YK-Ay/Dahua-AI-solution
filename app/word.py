"""Word 渲染器：Spec → .docx（python-docx，本地生成，不联网）。"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from config.settings import DB_PATH

from .builder import DISK_CAPACITY_TB, NVR_CHANNELS, SWITCH_PORTS
from .calc import DEFAULT_BANDWIDTH_OVERHEAD, GB_PER_DAY_PER_MBPS
from .models import Spec

CN_FONT = "微软雅黑"
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
MUTED = RGBColor(0x6B, 0x7A, 0x8A)


def _run_font(run, size: float = 10.5, bold: bool = False, color: RGBColor | None = None) -> None:
    run.font.name = CN_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), CN_FONT)


def _style_font(style, size: float | None = None) -> None:
    style.font.name = CN_FONT
    style.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)
    if size:
        style.font.size = Pt(size)


def _shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _para(doc, text: str, size: float = 10.5, bold: bool = False,
          color: RGBColor | None = None, align=None, space_after: float = 4):
    p = doc.add_paragraph()
    run = p.add_run(text)
    _run_font(run, size, bold, color)
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    return p


def _heading(doc, text: str, level: int = 1) -> None:
    h = doc.add_heading(level=level)
    run = h.add_run(text)
    _run_font(run, 15 if level == 1 else 12.5, True, ACCENT)


def _bullet(doc, text: str) -> None:
    p = doc.add_paragraph(style="List Bullet")
    run = p.add_run(text)
    _run_font(run, 10)
    p.paragraph_format.space_after = Pt(3)


def _table(doc, headers: list[str], rows: list[list], widths: list[float] | None = None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, head in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(head)
        _run_font(r, 9.5, True, RGBColor(0xFF, 0xFF, 0xFF))
        _shade(cell, "243447")
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            r = p.add_run(str(value))
            _run_font(r, 9)
    if widths:
        for row in table.rows:
            for i, width in enumerate(widths):
                row.cells[i].width = Cm(width)
    return table


SOURCE_LABEL = {
    "": "大华云商公开产品池", "legacy": "大华云商公开产品池",
    "official_catalog": "大华官网产品目录",
    "mall_public_search": "大华云商公开检索",
    "bom_prm": "云商配单·项目产品",
    "bom_virtual": "云商配单·虚拟/服务项",
}


def _source_section(doc, spec: Spec) -> None:
    """改造清单 Step 5：Word 附录「数据来源与测算口径」，全部取自本地知识库。"""
    _heading(doc, "八、数据来源与测算口径")
    _para(doc, "8.1 设备选型来源（本地知识库，逐条可回溯）", 10.5, True, space_after=4)

    rows: list[list] = []
    urls: list[str] = []
    counts: dict[str, int] = {}
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        for i, d in enumerate(spec.devices, 1):
            r = con.execute(
                "SELECT source, source_url, collected_at, price_stand FROM products WHERE model=?",
                (d.model,),
            ).fetchone()
            if r:
                label = SOURCE_LABEL.get(r["source"] or "", r["source"] or "大华云商公开产品池")
                price = f"{r['price_stand']:,.2f}" if r["price_stand"] else "未公开"
                when = (r["collected_at"] or "")[:10] or "—"
                url = r["source_url"] or ""
                if url and url not in urls:
                    urls.append(url)
            else:
                label, price, when = "本地规则库（产品库未命中）", "—", "—"
            rows.append([i, d.model, d.display_name or "—", label, price, when])
        for t in ("products", "solutions", "boms", "bom_items", "pages",
                  "kb_docs", "competitor_facts", "competitor_products"):
            try:
                counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                counts[t] = 0
    finally:
        con.close()

    _table(doc, ["序号", "型号", "名称", "资料来源", "标准价(元)", "采集日期"],
           rows, [1.2, 4.0, 4.6, 3.6, 2.0, 2.2])
    if urls:
        _para(doc, "来源页面（官网公开资料，链接仅用于溯源，离线时数据仍在本机）：",
              9, False, MUTED, space_after=2)
        for u in urls[:12]:
            _bullet(doc, u)

    _para(doc, "8.2 测算口径（数字由确定性函数计算，不由大模型生成）", 10.5, True, space_after=4)
    for line in (
        f"GB/天/路 = 码流(Mbps) × 86400 / 8 / 1024 = 码流 × {GB_PER_DAY_PER_MBPS:.4f}",
        f"裸容量 = Σ(点位数 × GB/天/路) × {spec.storage.retention_days} 天 / 1024",
        f"冗余容量 = 裸容量 × {spec.storage.redundancy} 冗余系数",
        f"上行带宽 = Σ(点位数 × 码流) × {DEFAULT_BANDWIDTH_OVERHEAD} 并发系数",
        f"设备折算：NVR {NVR_CHANNELS} 路/台、硬盘 {DISK_CAPACITY_TB} TB/块、"
        f"PoE 交换机 {SWITCH_PORTS} 口/台",
        "价格口径：产品库标准价（未税、未折扣）；云商配单同一分类下多个型号为可替换候选，"
        "应择一选型，不可累加成采购总价。",
    ):
        _bullet(doc, line)

    _para(doc, "8.3 本地知识库构成（单文件 SQLite，无需联网）", 10.5, True, space_after=4)
    for line in (
        f"产品与图片：products {counts.get('products', 0)} 条（含官网目录、云商公开池与配单溯源型号）",
        f"方案与配单：solutions {counts.get('solutions', 0)} 条、boms {counts.get('boms', 0)} 份、"
        f"bom_items {counts.get('bom_items', 0)} 条明细",
        f"行业案例与方案文档：pages {counts.get('pages', 0)} 条、kb_docs {counts.get('kb_docs', 0)} 篇（官网原文，非 AI 总结）",
        f"竞品参数：competitor_products {counts.get('competitor_products', 0)} 款机型、"
        f"competitor_facts {counts.get('competitor_facts', 0)} 条官网参数事实",
    ):
        _bullet(doc, line)
    _para(doc, "本章所有数据均来自本地知识库，生成过程不联网；示例项目信息为演示用虚构数据。",
          9, False, MUTED, space_after=8)


def render_docx(spec: Spec, out_path: Path | str, topology_png: Path | str | None = None) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    section = doc.sections[0]
    section.left_margin = section.right_margin = Cm(2)
    section.top_margin = section.bottom_margin = Cm(2)
    _style_font(doc.styles["Normal"], 10.5)
    for name in ["Heading 1", "Heading 2"]:
        _style_font(doc.styles[name])

    _para(doc, spec.project.name, 20, True, ACCENT, WD_ALIGN_PARAGRAPH.CENTER, 2)
    _para(doc, "安防解决方案（AI 生成草稿）", 12, False, MUTED, WD_ALIGN_PARAGRAPH.CENTER, 6)
    _para(doc, f"行业：{spec.project.industry}　|　生成日期：{date.today().isoformat()}　|　"
               f"生成方式：Spec 渲染（数字由确定性函数计算）", 9, False, MUTED, WD_ALIGN_PARAGRAPH.CENTER, 14)

    _heading(doc, "一、项目概况")
    _para(doc, f"项目名称：{spec.project.name}")
    _para(doc, f"所属行业：{spec.project.industry}")
    tags = "、".join(spec.project.industry_tags) or "—"
    _para(doc, f"场景标签：{tags}")
    scale = spec.project.scale
    parts = []
    if scale.buildings:
        parts.append(f"建筑 {scale.buildings} 栋")
    if scale.area_ha:
        parts.append(f"占地 {scale.area_ha} 公顷")
    if scale.entrances:
        parts.append(f"出入口 {scale.entrances} 个")
    _para(doc, f"项目规模：{' / '.join(parts) if parts else '需求未提供，待补'}")

    _heading(doc, "二、需求分解")
    _table(
        doc,
        ["场景", "建设目标", "重要级别", "点位数", "码流 (Mbps)"],
        [[r.target, r.intent, r.level, r.lanes, f"{r.bitrate_mbps:g}"] for r in spec.requirements],
        [4.0, 5.2, 2.4, 2.4, 3.0],
    )

    _heading(doc, "三、设备清单")
    device_rows = []
    total_cost = 0.0
    for idx, d in enumerate(spec.devices, 1):
        subtotal = (d.unit_price or 0) * d.qty
        total_cost += subtotal
        device_rows.append([
            idx, d.category, d.model, d.display_name or "—", d.qty,
            f"{d.unit_price:,.2f}" if d.unit_price is not None else "待询价",
            f"{subtotal:,.2f}" if d.unit_price is not None else "—",
            d.reason or "—",
        ])
    device_rows.append(["", "", "", "合计（标准价，未税/未折扣）", "", "", f"{total_cost:,.2f}", ""])
    _table(
        doc,
        ["序号", "类别", "型号", "名称", "数量", "单价(元)", "小计(元)", "选型依据"],
        device_rows,
        [0.9, 2.0, 3.2, 3.6, 1.2, 1.7, 1.9, 4.2],
    )

    _heading(doc, "四、存储与带宽测算")
    total_bitrate = sum(r.lanes * r.bitrate_mbps for r in spec.requirements)
    gb_day = round(total_bitrate * 10.5469, 1)
    _bullet(doc, f"总码流：Σ(点位数 × 码流) = {total_bitrate:g} Mbps")
    _bullet(doc, f"数据量：{total_bitrate:g} Mbps × 10.5469 = {gb_day} GB/天")
    _bullet(doc, f"裸容量：{gb_day} GB/天 × {spec.storage.retention_days} 天 / 1024 = {spec.storage.raw_tb} TB")
    _bullet(doc, f"冗余容量：{spec.storage.raw_tb} TB × {spec.storage.redundancy} = {spec.storage.with_redundancy_tb} TB")
    _bullet(doc, f"上行带宽：{total_bitrate:g} Mbps × 1.2 并发系数 = {spec.network.uplink_mbps} Mbps")
    _para(doc, "口径：GB/天/路 ≈ 码流(Mbps) × 86400 / 8 / 1024；所有参数可在 Spec 中显式调整。",
          9, False, MUTED, space_after=8)

    if topology_png and Path(topology_png).exists():
        _heading(doc, "五、系统拓扑")
        doc.add_picture(str(topology_png), width=Cm(16.5))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        _para(doc, "示意图：前端 → 传输 → 存储 → 平台 → 显示（平台层为 P1 规划）。",
              8.5, False, MUTED, WD_ALIGN_PARAGRAPH.CENTER, 10)

    _heading(doc, "六、交付边界")
    _para(doc, "包含：", 10.5, True, space_after=2)
    for item in spec.boundaries.included:
        _bullet(doc, item)
    _para(doc, "不包含：", 10.5, True, space_after=2, color=RGBColor(0xB0, 0x30, 0x30))
    for item in spec.boundaries.excluded:
        _bullet(doc, item)
    _para(doc, "关键假设：", 10.5, True, space_after=2)
    for item in spec.boundaries.assumptions:
        _bullet(doc, item)

    _heading(doc, "七、缺口与待确认")
    if spec.gaps:
        for gap in spec.gaps:
            _bullet(doc, gap)
    else:
        _para(doc, "无。需求信息完整，可直接进入报价与深化设计。")

    _source_section(doc, spec)

    _para(doc, "免责声明：本文档由程序按 Spec 自动生成，仅作售前草稿；设备型号与价格来自本地知识库"
               "（原始来源为大华云商公开页面），示例项目数据为虚构，交付前须人工核对技术参数、报价与合规要求。",
          8.5, False, MUTED, space_after=0)

    doc.save(out)
    return out
