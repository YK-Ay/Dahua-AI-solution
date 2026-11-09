"""竞品对比数据层单测：类目产品池、自定义配对、反幻觉（缺项不编造）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import compare_service as CS  # noqa: E402
from config.settings import DB_PATH  # noqa: E402


class CompareServiceTest(unittest.TestCase):
    def test_parse_spec_value_groups_and_continuation(self):
        text = ("【摄像机】\n像素 | 800万\n最大分辨率 | 3840×2160\n"
                "【视频】\n视频压缩标准 | H.265；H.264\n"
                "最低照度 | 彩色：0.002lux；\n黑白：0lux")
        got = CS.parse_spec_value(text)
        self.assertEqual(got["像素"], "800万")
        self.assertNotIn("【摄像机】", got)
        self.assertIn("黑白：0lux", got["最低照度"])

    def test_categories_cover_all_three_device_types(self):
        cats = CS.categories()["items"]
        self.assertEqual([c["key"] for c in cats], ["网络摄像机", "NVR", "PoE交换机"])
        for c in cats:
            self.assertGreater(c["dahua_products"], 0)
            self.assertGreaterEqual(c["hik_models"], 1)

    def test_dahua_pool_puts_products_with_spec_first(self):
        data = CS.products("网络摄像机", "大华", limit=10)
        self.assertGreater(data["total"], 100)
        flags = [x["has_spec"] for x in data["items"]]
        self.assertEqual(flags, sorted(flags, reverse=True))

    def test_hik_pool_is_model_level_not_fact_rows(self):
        data = CS.products("网络摄像机", "海康")
        self.assertGreaterEqual(data["total"], 1)
        self.assertTrue(all(x["fact_count"] > 0 for x in data["items"]))
        self.assertEqual(len({x["model"] for x in data["items"]}), len(data["items"]))

    def test_official_representatives_use_precise_comparison(self):
        r = CS.pair("网络摄像机", "DH-IPC-HFW5443M1-YL-PV-AS", "DS-2CD1245(D)-LA")
        self.assertEqual(r["mode"], "official_pair")
        self.assertEqual(len(r["rows"]), 8)

    def test_custom_pair_aligns_real_specs(self):
        r = CS.pair("网络摄像机", "DH-IPC-HFW5859M3-PV-AS-JM", "DS-2CD1245(D)-LA")
        self.assertEqual(r["mode"], "custom_pair")
        self.assertEqual(r["coverage"]["total"], 8)
        self.assertGreaterEqual(r["coverage"]["aligned"], 6)
        pixel = next(x for x in r["rows"] if x["field"] == "像素/分辨率")
        self.assertIn("800", pixel["dahua_value"])

    def test_unknown_model_rejected_not_invented(self):
        with self.assertRaises(ValueError):
            CS.pair("网络摄像机", "DH-NOT-EXIST-0000", "DS-2CD1245(D)-LA")
        with self.assertRaises(ValueError):
            CS.pair("网络摄像机", "DH-IPC-HFW5859M3-PV-AS-JM", "DS-NOT-EXIST")

    def _catalog_ready(self):
        import sqlite3
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            con.execute("SELECT 1 FROM competitor_products LIMIT 1").fetchone()
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            con.close()

    def test_hik_pool_expanded_after_crawl(self):
        if not self._catalog_ready():
            self.skipTest("尚未执行 scraper.collect_hik_catalog")
        for cat in ("网络摄像机", "NVR", "PoE交换机"):
            data = CS.products(cat, "海康", limit=5)
            self.assertGreater(data["total"], 10, f"{cat} 海康候选应显著多于补采前的 1 款")
            self.assertTrue(all(x["has_spec"] for x in data["items"]))

    def test_official_pair_never_mixes_models(self):
        r = CS.pair("网络摄像机", "DH-IPC-HFW5443M1-YL-PV-AS", "DS-2CD1245(D)-LA")
        self.assertEqual(r["mode"], "official_pair")
        self.assertEqual(r["dahua_model"], "DH-IPC-HFW5443M1-YL-PV-AS")
        self.assertEqual(r["hik_model"], "DS-2CD1245(D)-LA")
        self.assertEqual(len(r["rows"]), 8)

    def test_cli_compare_still_uses_representatives(self):
        from app.compare import compare
        r = compare("网络摄像机")
        self.assertEqual(r["hik_model"], "DS-2CD1245(D)-LA")

    def test_custom_pair_with_crawled_hik_model(self):
        if not self._catalog_ready():
            self.skipTest("尚未执行 scraper.collect_hik_catalog")
        data = CS.products("NVR", "海康", limit=30)
        target = next((x["model"] for x in data["items"]
                       if x["model"] not in ("DS-7700N-K4-V3", "DS-7800N-Q1")), None)
        if not target:
            self.skipTest("没有可用的补采型号")
        r = CS.pair("NVR", "DH-NVR5532-I", target)
        self.assertEqual(r["mode"], "custom_pair")
        self.assertEqual(r["hik_model"], target)
        self.assertEqual(r["coverage"]["total"], 7)

    def test_db_still_intact_after_changes(self):
        import sqlite3
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            self.assertGreaterEqual(con.execute("SELECT COUNT(*) FROM solutions").fetchone()[0], 53)
            self.assertGreaterEqual(con.execute("SELECT COUNT(*) FROM bom_items").fetchone()[0], 1093)
            self.assertGreaterEqual(con.execute("SELECT COUNT(*) FROM boms").fetchone()[0], 53)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
