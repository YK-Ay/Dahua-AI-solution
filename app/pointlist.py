"""点位表导入：Excel 行记录 → 结构化需求草稿。

点位表格式在不同项目之间差异很大，因此这里不强制单一模板：
通过常见中文表头别名识别区域、场景、数量和存储天数，无法确认的列保留在
原始数据中并返回 warning，不把猜测写成正式需求。
"""
from __future__ import annotations

import io
import re
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook


class PointListError(ValueError):
    """点位表无法读取或缺少可识别字段。"""


ALIASES = {
    "location": ("区域", "位置", "安装位置", "点位位置", "监控区域", "楼栋", "房间", "地点"),
    "scene": ("场景", "点位类型", "监控类型", "摄像机类型", "设备类型", "需求类型", "用途"),
    "qty": ("数量", "路数", "点位数", "摄像机数量", "设备数量", "个数"),
    "days": ("存储天数", "保存天数", "录像天数", "保存周期", "录像保存"),
    "industry": ("行业", "项目行业", "应用行业"),
}


def _norm(value: object) -> str:
    return re.sub(r"[\s　_\-（）()\[\]【】:/：]+", "", str(value or "")).lower()


def _header_kind(value: object) -> str | None:
    key = _norm(value)
    if not key:
        return None
    for kind, names in ALIASES.items():
        if key in {_norm(x) for x in names}:
            return kind
    for kind, names in ALIASES.items():
        if any(_norm(x) in key or key in _norm(x) for x in names):
            return kind
    return None


def _number(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return int(float(m.group())) if m else None


def _find_header(rows: list[tuple], max_rows: int = 8) -> tuple[int, dict[str, int]] | None:
    for idx, values in enumerate(rows[:max_rows]):
        found: dict[str, int] = {}
        for col, value in enumerate(values):
            kind = _header_kind(value)
            if kind and kind not in found:
                found[kind] = col
        if "scene" in found or "location" in found or "qty" in found:
            return idx, found
    return None


def _read_sheet(ws, sheet_name: str) -> tuple[list[dict], list[str]]:
    raw = [tuple(row) for row in ws.iter_rows(values_only=True)]
    header = _find_header(raw)
    if not header:
        return [], [f"工作表「{sheet_name}」未识别到区域、场景或数量表头"]
    header_row, columns = header
    rows: list[dict] = []
    warnings: list[str] = []
    for row_no, values in enumerate(raw[header_row + 1:], header_row + 2):
        if not any(str(x or "").strip() for x in values):
            continue
        def get(kind: str) -> str:
            col = columns.get(kind)
            return str(values[col] or "").strip() if col is not None and col < len(values) else ""

        location, scene = get("location"), get("scene")
        qty = _number(get("qty"))
        days = _number(get("days"))
        if not location and not scene:
            warnings.append(f"工作表「{sheet_name}」第 {row_no} 行缺少区域/场景，已跳过")
            continue
        if not scene:
            scene = location
        if qty is None:
            qty = 1
        if qty <= 0:
            warnings.append(f"工作表「{sheet_name}」第 {row_no} 行数量无效，已跳过")
            continue
        rows.append({
            "sheet": sheet_name,
            "row": row_no,
            "location": location,
            "scene": scene,
            "qty": qty,
            "retention_days": days,
            "industry": get("industry"),
        })
    return rows, warnings


def parse_xlsx(data: bytes, filename: str = "点位表.xlsx") -> dict:
    """读取 xlsx/xlsm 点位表，返回行记录、汇总、草稿和警告。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise PointListError("点位表目前支持 .xlsx / .xlsm，请先另存为新版 Excel 文件")
    if not data:
        raise PointListError("点位表文件为空")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise PointListError(f"Excel 打不开：{type(exc).__name__}") from exc
    try:
        all_rows: list[dict] = []
        warnings: list[str] = []
        sheets: list[str] = []
        for ws in wb.worksheets:
            rows, ws_warnings = _read_sheet(ws, ws.title)
            if rows:
                sheets.append(ws.title)
                all_rows.extend(rows)
            warnings.extend(ws_warnings)
    finally:
        wb.close()
    if not all_rows:
        raise PointListError("没有识别到有效点位行，请确认表头包含区域、场景或数量")

    grouped: dict[str, int] = defaultdict(int)
    locations: dict[str, set[str]] = defaultdict(set)
    industries = Counter()
    retention = Counter()
    for row in all_rows:
        key = row["scene"] or row["location"]
        grouped[key] += row["qty"]
        if row["location"]:
            locations[key].add(row["location"])
        if row["industry"]:
            industries[row["industry"]] += 1
        if row["retention_days"]:
            retention[row["retention_days"]] += 1

    parts = []
    for scene, qty in grouped.items():
        areas = "、".join(sorted(locations[scene]))
        parts.append(f"{scene}{qty}路" + (f"（{areas}）" if areas and areas != scene else ""))
    days = retention.most_common(1)[0][0] if retention else None
    industry = industries.most_common(1)[0][0] if industries else ""
    prefix = f"{industry}项目，" if industry else "点位表导入项目，"
    draft = prefix + "，".join(parts) + (f"，录像保存{days}天" if days else "") + "。"
    return {
        "sheets": sheets,
        "rows": all_rows,
        "summary": {
            "rows": len(all_rows),
            "scenes": len(grouped),
            "lanes": sum(grouped.values()),
            "retention_days": days,
            "industry": industry,
            "by_scene": [{"scene": k, "qty": v} for k, v in grouped.items()],
        },
        "draft": draft,
        "warnings": warnings[:30],
    }
