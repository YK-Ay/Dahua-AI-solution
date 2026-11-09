"""W4 竞品对比单测：归一化解析、判定逻辑、全类目对比与 DOCX 导出。"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from app.compare import FIELDS, _verdict, compare, export_docx, p_distance, p_pixel, p_ports
from config.settings import DB_PATH

STATUSES = {"same", "dahua", "hik", "qualitative", "only_dahua", "only_hik"}


def _field(category: str, name: str) -> dict:
    return next(f for f in FIELDS[category] if f["field"] == name)


def has_facts() -> bool:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        return conn.execute("SELECT COUNT(*) FROM competitor_facts").fetchone()[0] > 0
    except sqlite3.Error:
        return False


class ParseTest(unittest.TestCase):
    def test_pixel_same_tier_with_tolerance(self):
        # 大华 400 万 vs 海康 2560×1440（约 368.6 万），差异 <10% → 同档
        a, b = p_pixel("400万"), p_pixel("2560 × 1440")
        self.assertEqual(a.num, 400.0)
        self.assertAlmostEqual(b.num, 368.6, places=1)
        status, _, note = _verdict(_field("网络摄像机", "像素/分辨率"), a, b)
        self.assertEqual(status, "same")
        self.assertIn("容差", note)

    def test_light_distance_numeric(self):
        a, b = p_distance("60m（红外）；30m（暖光）；2m（RL检测距离）"), p_distance("红外光最远可达30 m，白光最远可达30 m")
        self.assertEqual(a.num, 60.0)
        self.assertEqual(b.num, 30.0)
        status, delta, _ = _verdict(_field("网络摄像机", "补光距离"), a, b)
        self.assertEqual(status, "dahua")
        self.assertIn("+30 m", delta)

    def test_port_count_and_speed(self):
        a, b = p_ports("1个（RJ-45网口，支持10M/100M 网络数据）"), p_ports("1个RJ45 10 M/100 M自适应以太网口")
        self.assertEqual((a.num, a.tokens), (1.0, (100,)))
        self.assertEqual((b.num, b.tokens), (1.0, (100,)))
        status, _, _ = _verdict(_field("网络摄像机", "网络接口"), a, b)
        self.assertEqual(status, "same")

    def test_non_numeric_field_not_asserted(self):
        # 海康"数字宽动态"无 dB 值 → 口径不同，不做数值断言
        from app.compare import p_db
        a, b = p_db("120dB"), p_db("数字宽动态")
        status, _, note = _verdict(_field("网络摄像机", "宽动态"), a, b)
        self.assertEqual(status, "qualitative")
        self.assertTrue(note)


@unittest.skipUnless(has_facts(), "竞品事实库未采集，跳过对比库测试")
class CompareDbTest(unittest.TestCase):
    def test_all_categories_have_evidence(self):
        for category in FIELDS:
            result = compare(category)
            self.assertTrue(result["rows"], category)
            for row in result["rows"]:
                self.assertIn(row["status"], STATUSES)
                self.assertTrue(row["talking_point"].startswith(
                    ("同档", "我方占优", "对方占优", "口径不同", "仅大华公开", "仅海康公开")))
                if row["status"] in {"same", "dahua", "hik", "qualitative"}:
                    self.assertTrue(row["dahua_source"] and row["hik_source"])
                    self.assertTrue(row["dahua_evidence"] and row["hik_evidence"])
                if row["status"] == "only_dahua":
                    self.assertFalse(row["hik_source"])

    def test_export_docx(self):
        result = compare("网络摄像机")
        with tempfile.TemporaryDirectory() as tmp:
            out = export_docx(result, Path(tmp) / "compare.docx")
            self.assertGreater(out.stat().st_size, 10_000)
            doc = Document(str(out))
            self.assertEqual(len(doc.tables), 3)
            self.assertGreater(len(doc.paragraphs), 10)


if __name__ == "__main__":
    unittest.main()
