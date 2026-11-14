"""技术偏离表测试：条款解析、可计算条款判定、Excel 读写。"""
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook, load_workbook

from app.builder import build_spec
from app.deviations import (evaluate, parse_text, parse_xlsx, render_xlsx,
                            score_index, summarize)
from config.settings import DB_PATH

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


class ParseTest(unittest.TestCase):
    def test_parse_text_strips_numbering_and_weight(self):
        clauses = parse_text("1. 存储录像保存时间不少于 30 天（3分）\n\n2、总点位数不少于 100 路")
        self.assertEqual(len(clauses), 2)
        self.assertEqual(clauses[0]["text"], "存储录像保存时间不少于 30 天")
        self.assertEqual(clauses[0]["weight"], "3")
        self.assertEqual(clauses[1]["text"], "总点位数不少于 100 路")

    def test_parse_xlsx_with_header(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["序号", "技术要求", "权重"])
        ws.append([1, "存储录像保存时间不少于 60 天", "5"])
        ws.append([2, "平台支持 GB/T 28181 联网", "3"])
        buf = io.BytesIO()
        wb.save(buf)

        clauses = parse_xlsx(buf.getvalue())
        self.assertEqual(len(clauses), 2)
        self.assertIn("存储录像", clauses[0]["text"])
        self.assertEqual(clauses[0]["weight"], "5")


@unittest.skipUnless(DB_PATH.exists(), "知识库不存在，跳过偏离表判定测试")
class EvaluateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = build_spec(TEXT)

    def test_numeric_respond_and_negative(self):
        rows = evaluate(parse_text(
            "1. 存储录像保存时间不少于 30 天\n"
            "2. 存储录像保存时间不少于 90 天\n"
            "3. 总点位数不少于 20 路\n"
            "4. 单路摄像机码流不低于 2Mbps"
        ), self.spec)
        self.assertEqual(rows[0]["status"], "respond")
        self.assertEqual(rows[1]["status"], "negative")
        self.assertEqual(rows[2]["status"], "respond")
        self.assertEqual(rows[3]["status"], "respond")
        summary = summarize(rows)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["negative"], 1)

    def test_unmatched_clause_is_confirm(self):
        rows = evaluate(parse_text("投标人须具备涉密信息系统集成甲级资质"), self.spec)
        self.assertEqual(rows[0]["status"], "confirm")
        self.assertIn("待确认", rows[0]["status_label"])

    def test_render_xlsx_with_score_sheet(self):
        rows = evaluate(parse_text(
            "1. 存储录像保存时间不少于 30 天（5分）\n"
            "2. 平台支持 GB/T 28181 联网（3分）"
        ), self.spec)
        scores = score_index(rows)
        self.assertEqual(len(scores), 2)
        with tempfile.TemporaryDirectory() as tmp:
            out = render_xlsx(rows, summarize(rows), "测试项目", scores, Path(tmp) / "dev.xlsx")
            wb = load_workbook(out)
            self.assertEqual(wb.sheetnames, ["技术偏离表", "评分点索引"])
            head = [c.value for c in wb["技术偏离表"][1]]
            self.assertIn("响应结论", head)
            self.assertIn("依据（型号/参数）", head)


if __name__ == "__main__":
    unittest.main()
