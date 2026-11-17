"""点位表导入：常见表头、汇总、草稿和错误提示。"""
import io
import sys
import unittest
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pointlist import PointListError, parse_xlsx  # noqa: E402


def workbook_bytes(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "点位表"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class PointListTest(unittest.TestCase):
    def test_parse_and_aggregate_common_headers(self):
        data = workbook_bytes([
            ["区域", "监控类型", "数量", "录像保存天数", "行业"],
            ["教学楼", "室内", 20, 90, "智慧校园"],
            ["宿舍楼", "室内", 12, 90, "智慧校园"],
            ["校门", "出入口", 4, 90, "智慧校园"],
        ])
        result = parse_xlsx(data, "校园点位表.xlsx")
        self.assertEqual(result["summary"]["rows"], 3)
        self.assertEqual(result["summary"]["lanes"], 36)
        self.assertEqual(result["summary"]["retention_days"], 90)
        self.assertIn("智慧校园项目", result["draft"])
        self.assertIn("室内32路", result["draft"])

    def test_row_without_quantity_counts_as_one(self):
        data = workbook_bytes([
            ["安装位置", "场景"],
            ["一层大厅", "公共区域"],
            ["二层大厅", "公共区域"],
        ])
        result = parse_xlsx(data)
        self.assertEqual(result["summary"]["lanes"], 2)
        self.assertEqual(result["summary"]["scenes"], 1)

    def test_invalid_format_rejected(self):
        with self.assertRaises(PointListError):
            parse_xlsx(b"not excel", "点位表.xls")

    def test_unknown_rows_are_reported(self):
        data = workbook_bytes([
            ["区域", "场景", "数量"],
            ["教学楼", "室内", 10],
            ["", "", ""],
        ])
        result = parse_xlsx(data)
        self.assertEqual(result["summary"]["lanes"], 10)


if __name__ == "__main__":
    unittest.main()
