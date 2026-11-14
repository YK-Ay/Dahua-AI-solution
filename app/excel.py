"""Excel 交付物：报价单与点位统计表（openpyxl，本地生成，不联网）。

只输出 Spec 里已有的数据，不补猜价格或参数：
  - 报价单：设备明细 + 标准价合计，缺价型号明确标注「待询价」
  - 点位表：按场景汇总点位数、码流、日增数据与所需容量
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .calc import GB_PER_DAY_PER_MBPS
from .models import Spec
from .quote import build_quote
from .engineering_checks import evaluate as evaluate_engineering

FONT = "微软雅黑"
HEAD_FILL = PatternFill("solid", fgColor="243447")
NOTE_FILL = PatternFill("solid", fgColor="F2F4F7")
THIN = Side(style="thin", color="D6DAE0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _title(ws, text: str, span: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    cell = ws.cell(row=1, column=1, value=text)
    cell.font = Font(name=FONT, size=14, bold=True, color="1F4E79")
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28


def _header(ws, row: int, headers: list[str]) -> None:
    for i, head in enumerate(headers, 1):
        cell = ws.cell(row=row, column=i, value=head)
        cell.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    ws.row_dimensions[row].height = 22


def _body(ws, row: int, values: list, number_cols: set[int] = frozenset()) -> None:
    for i, value in enumerate(values, 1):
        cell = ws.cell(row=row, column=i, value=value)
        cell.font = Font(name=FONT, size=10)
        cell.border = BORDER
        cell.alignment = Alignment(
            horizontal="right" if i in number_cols else "left", vertical="center")
        if i in number_cols and isinstance(value, (int, float)):
            cell.number_format = "#,##0.00"


def _widths(ws, widths: list[float]) -> None:
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _note(ws, row: int, text: str, span: int) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = Font(name=FONT, size=9, color="5B6672")
    cell.fill = NOTE_FILL
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    return row + 1


def _meta(ws, row: int, pairs: list[tuple[str, str]], span: int) -> int:
    for key, value in pairs:
        ws.cell(row=row, column=1, value=key).font = Font(name=FONT, size=10, bold=True)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=span)
        ws.cell(row=row, column=2, value=value).font = Font(name=FONT, size=10)
        row += 1
    return row


def render_quote_xlsx(spec: Spec | dict, out_path: Path | str) -> Path:
    """报价单：明细 + 标准价合计（未税、未折扣、未含施工与维保）。"""
    if isinstance(spec, dict):
        spec = Spec.model_validate(spec)
    quote = build_quote(spec)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "报价单"
    headers = ["序号", "类别", "型号", "名称", "使用场景", "数量",
               "单价(元)", "小计(元)", "备注"]
    _title(ws, f"{spec.project.name} · 设备报价单", len(headers))
    row = _meta(ws, 2, [
        ("项目名称", spec.project.name),
        ("报价日期", date.today().isoformat()),
        ("币种", "人民币 CNY"),
        ("报价口径", "产品库标准价（未税、未折扣、未含施工与维保）"),
    ], len(headers))
    row += 1
    _header(ws, row, headers)
    start = row + 1
    for line in quote["lines"]:
        row += 1
        _body(ws, row, [
            line["index"], line["category"], line["model"], line["display_name"] or "—",
            line["scene"] or "—", line["qty"],
            line["unit_price"] if line["unit_price"] is not None else "待询价",
            line["amount"] if line["amount"] is not None else "—",
            "" if line["unit_price"] is not None else "缺标准价，需询价后补入",
        ], {6, 7})
    row += 1
    ws.cell(row=row, column=1, value="合计")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    ws.cell(row=row, column=1).font = Font(name=FONT, size=11, bold=True)
    ws.cell(row=row, column=1).alignment = Alignment(horizontal="right")
    total = ws.cell(row=row, column=8, value=quote["total"])
    total.font = Font(name=FONT, size=11, bold=True, color="B03030")
    total.number_format = "#,##0.00"
    ws.cell(row=row, column=9, value=f"{quote['priced']}/{len(quote['lines'])} 项已定价")
    for col in range(1, len(headers) + 1):
        ws.cell(row=row, column=col).border = BORDER
        ws.cell(row=row, column=col).fill = NOTE_FILL
    row += 2
    _note(ws, row, quote["note"], len(headers))

    ws.freeze_panes = ws.cell(row=start, column=1)
    _widths(ws, [6, 14, 28, 30, 14, 8, 14, 14, 26])
    ws.auto_filter.ref = f"A{start - 1}:I{row - 3}"
    wb.save(out)
    return out


def render_point_table_xlsx(spec: Spec | dict, out_path: Path | str) -> Path:
    """点位表：按场景统计点位数、码流、日增数据与存储容量。"""
    if isinstance(spec, dict):
        spec = Spec.model_validate(spec)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "点位表"
    headers = ["序号", "场景", "建设目标", "重要级别", "点位数",
               "单路码流(Mbps)", "日增数据(GB)", "存储天数", "所需容量(TB)"]
    _title(ws, f"{spec.project.name} · 点位与容量统计表", len(headers))
    row = _meta(ws, 2, [
        ("项目名称", spec.project.name),
        ("所属行业", spec.project.industry),
        ("统计日期", date.today().isoformat()),
        ("测算口径", f"日增 = 点位数 × 码流 × {GB_PER_DAY_PER_MBPS:.4f}；容量 = 日增 × 天数 / 1024"),
    ], len(headers))
    row += 1
    _header(ws, row, headers)
    start = row + 1
    days = spec.storage.retention_days
    total_lanes = 0
    total_gb = 0.0
    for idx, req in enumerate(spec.requirements, 1):
        row += 1
        daily = req.lanes * req.bitrate_mbps * GB_PER_DAY_PER_MBPS
        capacity = daily * days / 1024
        total_lanes += req.lanes
        total_gb += daily
        _body(ws, row, [
            idx, req.target, req.intent, req.level, req.lanes,
            req.bitrate_mbps, round(daily, 1), days, round(capacity, 2),
        ], {5, 6, 7, 8, 9})
    row += 1
    ws.cell(row=row, column=1, value="合计")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    ws.cell(row=row, column=1).font = Font(name=FONT, size=11, bold=True)
    ws.cell(row=row, column=1).alignment = Alignment(horizontal="right")
    ws.cell(row=row, column=5, value=total_lanes).font = Font(name=FONT, size=11, bold=True)
    ws.cell(row=row, column=7, value=round(total_gb, 1)).font = Font(name=FONT, size=11, bold=True)
    ws.cell(row=row, column=8, value=days).font = Font(name=FONT, size=11, bold=True)
    ws.cell(row=row, column=9, value=round(total_gb * days / 1024, 2)).font = Font(
        name=FONT, size=11, bold=True, color="B03030")
    for col in range(1, len(headers) + 1):
        ws.cell(row=row, column=col).border = BORDER
        ws.cell(row=row, column=col).fill = NOTE_FILL

    row += 2
    _note(ws, row, "本表由确定性公式计算，点位与码流取自需求解析结果；"
                   "施工点位的楼栋、楼层与坐标需现场勘察后补充。", len(headers))
    ws.freeze_panes = ws.cell(row=start, column=1)
    _widths(ws, [6, 16, 32, 10, 9, 15, 15, 11, 14])
    wb.save(out)
    return out


def render_engineering_check_xlsx(spec: Spec | dict, out_path: Path | str) -> Path:
    """工程审查清单：把页面上的风险与人工确认项随方案包带走。"""
    if isinstance(spec, dict):
        spec = Spec.model_validate(spec)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = sum(r.lanes for r in spec.requirements)
    validation = evaluate_engineering(spec.model_dump(), f"{total}路，{spec.storage.retention_days}天")
    wb = Workbook()
    ws = wb.active
    ws.title = "工程审查"
    headers = ["级别", "检查项", "检查结果", "建议动作"]
    _title(ws, f"{spec.project.name} · 工程审查清单", len(headers))
    row = _meta(ws, 2, [("总体结论", validation["label"]),
                        ("统计", f"通过 {validation['summary']['pass']} · 复核 {validation['summary']['warn']} · 阻断 {validation['summary']['block']}"),
                        ("说明", "本表用于内部评审和交付前复核，不能替代现场勘察、厂家彩页和正式设计。")], len(headers))
    row += 1
    _header(ws, row, headers)
    for item in validation["items"]:
        row += 1
        label = {"pass": "通过", "warn": "需复核", "block": "阻断"}.get(item["level"], item["level"])
        _body(ws, row, [label, item["title"], item["detail"], item.get("action") or "—"])
    _widths(ws, [12, 28, 72, 58])
    ws.freeze_panes = "A6"
    for r in range(6, row + 1):
        ws.row_dimensions[r].height = 38
        for c in range(1, 5):
            ws.cell(r, c).alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(out)
    return out
