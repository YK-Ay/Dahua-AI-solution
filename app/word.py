"""Word 渲染器：Spec → .docx（python-docx，本地生成，不联网）。"""
from __future__ import annotations

import sqlite3
import zlib
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


def _source_section(doc, spec: Spec, title: str = "数据来源与测算口径") -> None:
    """Word 附录「数据来源与测算口径」，全部取自本地知识库。"""
    _heading(doc, title)
    _para(doc, "（一）设备选型来源（本地知识库，逐条可回溯）", 10.5, True, space_after=4)

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

    _para(doc, "（二）测算口径（数字由确定性函数计算）", 10.5, True, space_after=4)
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

    _para(doc, "（三）本地知识库构成（单文件 SQLite，无需联网）", 10.5, True, space_after=4)
    for line in (
        f"产品与图片：products {counts.get('products', 0)} 条（含官网目录、云商公开池与配单溯源型号）",
        f"方案与配单：solutions {counts.get('solutions', 0)} 条、boms {counts.get('boms', 0)} 份、"
        f"bom_items {counts.get('bom_items', 0)} 条明细",
        f"行业案例与方案文档：pages {counts.get('pages', 0)} 条、kb_docs {counts.get('kb_docs', 0)} 篇（官网原文摘录）",
        f"竞品参数：competitor_products {counts.get('competitor_products', 0)} 款机型、"
        f"competitor_facts {counts.get('competitor_facts', 0)} 条官网参数事实",
    ):
        _bullet(doc, line)
    _para(doc, "本章所有数据均来自本地知识库，生成过程不联网；示例项目信息为演示用虚构数据。",
          9, False, MUTED, space_after=8)


FRONT_TITLE_SIZE = 14


def _front_title(doc, text: str) -> None:
    """封面/修订/目录这类前置页标题：不进正文章节编号。"""
    p = doc.add_paragraph()
    run = p.add_run(text)
    _run_font(run, FRONT_TITLE_SIZE, True, ACCENT)
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(6)


def _project_no(name: str) -> str:
    return f"DH-{date.today():%Y%m%d}-{zlib.crc32(name.encode('utf-8')) % 10000:04d}"


def _meta_table(doc, rows: list[tuple[str, str]]) -> None:
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for key, value in rows:
        cells = table.add_row().cells
        cells[0].text = ""
        p = cells[0].paragraphs[0]
        _run_font(p.add_run(key), 10, True)
        cells[0].width = Cm(4)
        cells[1].text = ""
        p = cells[1].paragraphs[0]
        _run_font(p.add_run(value), 10)
        cells[1].width = Cm(12)


def _cover(doc, spec: Spec) -> None:
    for _ in range(4):
        _para(doc, "", 12, space_after=6)
    _para(doc, spec.project.name, 26, True, ACCENT, WD_ALIGN_PARAGRAPH.CENTER, 8)
    _para(doc, "安防系统技术方案", 18, True, None, WD_ALIGN_PARAGRAPH.CENTER, 26)
    _para(doc, "（售前技术文件）", 11, False, MUTED, WD_ALIGN_PARAGRAPH.CENTER, 36)
    _meta_table(doc, [
        ("项目名称", spec.project.name),
        ("项目编号", _project_no(spec.project.name)),
        ("所属行业", spec.project.industry),
        ("文件版本", "V1.0"),
        ("编制单位", "（待填写）"),
        ("编制日期", date.today().isoformat()),
        ("文件用途", "投标 / 技术交流 / 深化设计输入"),
    ])
    doc.add_page_break()

    _front_title(doc, "修订记录")
    _table(
        doc,
        ["版本", "日期", "修订内容", "编制", "审核"],
        [["V1.0", date.today().isoformat(), "初稿生成（依据需求解析与本地产品库）", "（待填写）", "（待填写）"]],
        [1.8, 3.0, 8.4, 2.4, 2.4],
    )

    _front_title(doc, "目录")
    _table(
        doc,
        ["章节", "主要内容"],
        [
            ["一、项目概述", "项目背景、建设范围与总体目标"],
            ["二、需求分析", "场景、建设目标、点位与码流"],
            ["三、设计依据与标准", "国家标准、行业标准与招标文件"],
            ["四、总体设计", "设计原则、系统架构与平台部署"],
            ["五、点位设计", "分场景点位统计与容量需求"],
            ["六、设备清单与选型说明", "型号、数量、单价与选型依据"],
            ["七、存储与带宽测算", "容量、冗余与上行带宽计算过程"],
            ["八、系统拓扑", "前端、传输、存储、平台、显示分层"],
            ["九、施工组织与进度", "实施阶段、交付物与参考周期"],
            ["十、售后服务与培训", "质保、响应、培训与备件"],
            ["十一、交付边界与风险假设", "包含项、不包含项与关键假设"],
            ["十二、缺口与待确认", "需甲方或现场补充的信息"],
            ["十三、数据来源与测算口径", "型号来源、公式与知识库构成"],
        ],
        [6.0, 12.0],
    )
    doc.add_page_break()


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

    _cover(doc, spec)

    # 一、项目概述
    _heading(doc, "一、项目概述")
    _para(doc, f"项目名称：{spec.project.name}")
    _para(doc, f"所属行业：{spec.project.industry}")
    _para(doc, f"场景标签：{'、'.join(spec.project.industry_tags) or '—'}")
    scale = spec.project.scale
    parts = []
    if scale.buildings:
        parts.append(f"建筑 {scale.buildings} 栋")
    if scale.area_ha:
        parts.append(f"占地 {scale.area_ha} 公顷")
    if scale.entrances:
        parts.append(f"出入口 {scale.entrances} 个")
    _para(doc, f"项目规模：{' / '.join(parts) if parts else '需求未提供，待补'}")
    lanes_total = sum(r.lanes for r in spec.requirements)
    _para(doc, f"建设规模：{len(spec.requirements)} 类场景、{lanes_total} 个点位，"
               f"存储周期 {spec.storage.retention_days} 天。")
    goals = "；".join(r.intent for r in spec.requirements[:6])
    _para(doc, f"建设目标：{goals or '按甲方需求确定'}。")
    _para(doc, "本文件依据甲方需求描述与本地产品库编制，用于技术交流、投标响应与深化设计输入；"
               "涉及现场条件、商务价格与服务承诺的内容，以招标文件、合同及现场勘察结果为准。")

    # 二、需求分析
    _heading(doc, "二、需求分析")
    _table(
        doc,
        ["序号", "场景", "建设目标", "重要级别", "点位数", "码流 (Mbps)", "日增数据 (GB)"],
        [[i, r.target, r.intent, r.level, r.lanes, f"{r.bitrate_mbps:g}",
          f"{r.lanes * r.bitrate_mbps * GB_PER_DAY_PER_MBPS:.1f}"]
         for i, r in enumerate(spec.requirements, 1)],
        [1.2, 4.0, 5.0, 1.8, 1.6, 2.2, 2.8],
    )
    _para(doc, f"合计 {lanes_total} 个点位、总码流 "
               f"{sum(r.lanes * r.bitrate_mbps for r in spec.requirements):g} Mbps。",
          9.5, False, MUTED, space_after=8)

    # 三、设计依据与标准
    _heading(doc, "三、设计依据与标准")
    for line in (
        "GB 50348-2018《安全防范工程技术标准》",
        "GB/T 28181-2022《公共安全视频监控联网系统信息传输、交换、控制技术要求》",
        "GB 51348-2019《民用建筑电气设计标准》",
        "GB 50606-2010《智能建筑工程施工规范》",
        "GB 50303-2015《建筑电气工程施工质量验收规范》",
        "GB/T 22239-2019《信息安全技术 网络安全等级保护基本要求》",
        "GA/T 1400《公安视频图像信息应用系统》",
        "项目招标文件、答疑澄清文件、设计图纸与现场勘察记录（待提供）",
    ):
        _bullet(doc, line)
    _para(doc, "以上为通用设计依据；若招标文件另有规定，以招标文件及合同约定为准。",
          9, False, MUTED, space_after=8)

    # 四、总体设计
    _heading(doc, "四、总体设计")
    _para(doc, "4.1 设计原则", 10.5, True, space_after=3)
    for line in (
        "合规性：设备与平台满足国家及行业标准，接口支持 GB/T 28181 联网。",
        "可靠性：存储采用冗余系数设计，关键设备按场景重要级别配置。",
        "可扩展：平台与传输按当前规模配置，预留点位与带宽扩展空间。",
        "可运维：统一平台管理，设备状态、录像完整性可集中查看。",
        "经济性：按场景选型，避免全量高配；同一分类提供可替换型号。",
    ):
        _bullet(doc, line)
    _para(doc, "4.2 系统架构", 10.5, True, space_after=3)
    _table(
        doc,
        ["层级", "主要组成", "说明"],
        [
            ["前端采集", "网络摄像机、球机、抓拍机", "按场景配置，负责图像采集与编码"],
            ["传输网络", "PoE 交换机、光纤/网线", "为前端供电并回传视频流"],
            ["存储", "NVR、硬盘", "本地录像存储，按容量测算配置"],
            ["平台", "管理平台 / 服务器", "设备管理、录像调度、联网上传"],
            ["显示", "监视器、解码设备", "值班室实时预览与回放"],
        ],
        [2.6, 6.0, 9.4],
    )
    _para(doc, "4.3 平台部署", 10.5, True, space_after=3)
    _para(doc, "平台按项目规模与联网要求部署，支持本级管理与上级平台对接。"
               "平台软件授权、license 数量与服务项需在商务阶段单独确认，不计入设备标准价。")

    # 五、点位设计
    _heading(doc, "五、点位设计")
    _table(
        doc,
        ["序号", "场景", "点位数", "单路码流 (Mbps)", "日增数据 (GB)",
         "存储天数", "所需容量 (TB)"],
        [[i, r.target, r.lanes, f"{r.bitrate_mbps:g}",
          f"{r.lanes * r.bitrate_mbps * GB_PER_DAY_PER_MBPS:.1f}",
          spec.storage.retention_days,
          f"{r.lanes * r.bitrate_mbps * GB_PER_DAY_PER_MBPS * spec.storage.retention_days / 1024:.2f}"]
         for i, r in enumerate(spec.requirements, 1)],
        [1.2, 5.0, 2.2, 3.0, 3.4, 2.4, 3.2],
    )
    _para(doc, "点位数量与码流取自需求解析结果；楼栋、楼层、安装高度与取电方式需现场勘察后补充，"
               "施工前应形成经甲方确认的点位表。", 9, False, MUTED, space_after=8)

    # 六、设备清单与选型说明
    _heading(doc, "六、设备清单与选型说明")
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
    _para(doc, "选型说明：型号来自本地产品库，同一分类下多个型号为可替换候选，应择一选型；"
               "价格为标准价（未税、未折扣），最终以商务报价单为准。", 9, False, MUTED, space_after=8)

    # 七、存储与带宽测算
    _heading(doc, "七、存储与带宽测算")
    total_bitrate = sum(r.lanes * r.bitrate_mbps for r in spec.requirements)
    gb_day = round(total_bitrate * GB_PER_DAY_PER_MBPS, 1)
    _bullet(doc, f"总码流：Σ(点位数 × 码流) = {total_bitrate:g} Mbps")
    _bullet(doc, f"数据量：{total_bitrate:g} Mbps × {GB_PER_DAY_PER_MBPS:.4f} = {gb_day} GB/天")
    _bullet(doc, f"裸容量：{gb_day} GB/天 × {spec.storage.retention_days} 天 / 1024 = {spec.storage.raw_tb} TB")
    _bullet(doc, f"冗余容量：{spec.storage.raw_tb} TB × {spec.storage.redundancy} = {spec.storage.with_redundancy_tb} TB")
    _bullet(doc, f"上行带宽：{total_bitrate:g} Mbps × {DEFAULT_BANDWIDTH_OVERHEAD} 并发系数 = {spec.network.uplink_mbps} Mbps")
    _para(doc, "口径：GB/天/路 ≈ 码流(Mbps) × 86400 / 8 / 1024；所有参数可在 Spec 中显式调整。",
          9, False, MUTED, space_after=8)

    # 八、系统拓扑
    _heading(doc, "八、系统拓扑")
    if topology_png and Path(topology_png).exists():
        doc.add_picture(str(topology_png), width=Cm(16.5))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        _para(doc, "示意图：前端 → 传输 → 存储 → 平台 → 显示；详细连线图随方案包提供。",
              8.5, False, MUTED, WD_ALIGN_PARAGRAPH.CENTER, 10)
    else:
        _para(doc, "拓扑图未随本文档生成，可在工作台重新导出。")

    # 九、施工组织与进度
    _heading(doc, "九、施工组织与进度")
    _table(
        doc,
        ["阶段", "主要工作", "交付物", "参考周期（周）"],
        [
            ["深化设计", "现场勘察、点位确认、图纸深化", "深化图纸、点位表", "1–2"],
            ["设备到货与报验", "设备采购、到货验收、资料报审", "到货清单、合格证", "1–2"],
            ["管线敷设与安装", "桥架管路、前端设备安装", "隐蔽工程记录", "2–4"],
            ["系统调试", "单机调试、联调、平台配置", "调试记录", "1–2"],
            ["试运行与培训", "试运行、培训、问题整改", "试运行报告", "2–4"],
            ["竣工验收", "资料整理、验收", "竣工资料", "1"],
        ],
        [3.0, 6.4, 4.4, 3.2],
    )
    _para(doc, "参考周期为示例，实际以合同工期、现场条件与设备到货情况为准。",
          9, False, MUTED, space_after=8)

    # 十、售后服务与培训
    _heading(doc, "十、售后服务与培训")
    for line in (
        "质保期：按合同约定执行（建议不少于 2 年）。",
        "响应机制：7×24 小时电话与远程支持；故障按影响程度分级响应，具体时限以商务承诺为准。",
        "培训对象：系统管理员、值班操作员、运维人员。",
        "培训内容：平台操作、录像回放与导出、设备日常维护、常见故障排查。",
        "培训方式：现场集中培训 + 操作手册 + 远程答疑。",
        "备品备件：按合同约定配置（建议摄像机、电源、交换机按比例备件）。",
        "服务交付物：操作手册、维护记录、巡检报告。",
    ):
        _bullet(doc, line)

    # 十一、交付边界与风险假设
    _heading(doc, "十一、交付边界与风险假设")
    _para(doc, "包含：", 10.5, True, space_after=2)
    for item in spec.boundaries.included:
        _bullet(doc, item)
    _para(doc, "不包含：", 10.5, True, space_after=2, color=RGBColor(0xB0, 0x30, 0x30))
    for item in spec.boundaries.excluded:
        _bullet(doc, item)
    _para(doc, "关键假设：", 10.5, True, space_after=2)
    for item in spec.boundaries.assumptions:
        _bullet(doc, item)

    # 十二、缺口与待确认
    _heading(doc, "十二、缺口与待确认")
    if spec.gaps:
        for gap in spec.gaps:
            _bullet(doc, gap)
    else:
        _para(doc, "无。需求信息完整，可直接进入报价与深化设计。")

    # 十三、数据来源与测算口径
    _source_section(doc, spec, "十三、数据来源与测算口径")

    _para(doc, "免责声明：本文件依据甲方需求与本地知识库生成，设备型号与价格来自公开资料；"
               "示例项目数据为虚构，正式投标前须人工核对技术参数、报价、服务承诺与合规要求。",
          8.5, False, MUTED, space_after=0)

    doc.save(out)
    return out
