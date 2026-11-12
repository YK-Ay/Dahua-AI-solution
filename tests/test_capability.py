"""设备能力标签回归：打标、排序、选型依据可解释、缺货替代提示。"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import capability as C  # noqa: E402
from app.builder import build_spec  # noqa: E402
from app.kb import ProductRepo  # noqa: E402
from app.taxonomy import SCENES, SCENE_DEFAULTS  # noqa: E402
from config.settings import DB_PATH  # noqa: E402


class CapabilityPureTest(unittest.TestCase):
    def test_tags_from_text(self):
        tags = C.tags_of_text("400万 防爆 双光谱测温 球机")
        self.assertIn("防爆", tags)
        self.assertIn("热成像测温", tags)
        self.assertIn("变倍云台", tags)

    def test_unknown_text_has_no_tags(self):
        self.assertEqual(C.tags_of_text("55英寸拼接单元"), C.tags_of_text("55英寸拼接单元"))
        self.assertNotIn("防爆", C.tags_of_text("55英寸拼接单元"))

    def test_describe_lists_matched(self):
        text = C.describe({"model": "TPC-BF1241", "matched": ["热成像测温"]})
        self.assertIn("TPC-BF1241", text)
        self.assertIn("热成像测温", text)


class CapabilityIndexTest(unittest.TestCase):
    def test_real_products_are_tagged(self):
        with ProductRepo() as repo:
            self.assertIn("热成像测温", C.tags_of(repo, "DH-TPC-BF1241"))
            self.assertIn("防爆", C.tags_of(repo, "DH-TPC-AEPT8441A"))

    def test_rank_prefers_overlap_then_price(self):
        with ProductRepo() as repo:
            cands = C.rank_candidates(repo, ["测温型枪型摄像机"], ["热成像测温"],
                                      require_price=False)
            self.assertTrue(cands, "测温类目里应有候选")
            top = cands[0]
            self.assertIn("热成像测温", top["matched"])
            self.assertEqual(top["class_name"], "测温型枪型摄像机")
            prices = [c["price_stand"] for c in cands if c["price_stand"] is not None]
            self.assertEqual(prices, sorted(prices))

    def test_empty_required_returns_nothing(self):
        with ProductRepo() as repo:
            self.assertEqual(C.rank_candidates(repo, [], []), [])

    def test_unknown_class_returns_nothing_but_cross_class_works(self):
        with ProductRepo() as repo:
            self.assertEqual(
                C.rank_candidates(repo, ["不存在的类目"], ["热成像测温"]), [])
            self.assertTrue(C.rank_candidates(repo, [], ["热成像测温"]))

    def test_index_rebuilds_when_db_changes(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "kb.sqlite"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE products (model TEXT, display_name TEXT, class_name TEXT, "
            "prod_desc TEXT, price_stand REAL, year_new TEXT)"
        )
        conn.execute(
            "INSERT INTO products VALUES ('X1', '防爆枪机', '防爆摄像机', '', 100, '2025')")
        conn.commit()
        conn.row_factory = sqlite3.Row
        self.assertIn("防爆", C.get_index(repo_of(conn))["X1"])
        conn.close()
        # 改库 + 刷新 mtime 后，索引应按新库重建
        conn2 = sqlite3.connect(path)
        conn2.row_factory = sqlite3.Row
        conn2.execute(
            "UPDATE products SET display_name = '普通枪机', class_name = '网络摄像机' "
            "WHERE model = 'X1'")
        conn2.commit()
        now = os.path.getmtime(path)
        os.utime(path, (now + 10, now + 10))
        self.assertNotIn("防爆", C.get_index(repo_of(conn2))["X1"])
        conn2.close()


class _Repo:
    def __init__(self, conn):
        self.conn = conn


def repo_of(conn):
    return _Repo(conn)


class CapabilityTaxonomyTest(unittest.TestCase):
    def test_all_capability_tags_are_known(self):
        for s in SCENES:
            for tag in s.get("capability_tags", []):
                self.assertIn(tag, C.CAPABILITY_RULES, f"{s['name']} 引用了未知能力 {tag}")

    def test_default_has_capability_tags(self):
        self.assertIn("capability_tags", SCENE_DEFAULTS)


class CapabilitySelectionTest(unittest.TestCase):
    def test_temperature_scene_reason_explains_capability(self):
        spec = build_spec("变电站设备测温，20路，存储30天")
        cam = next(d for d in spec.devices if d.scene == "变电设备测温")
        self.assertIn("能力命中", cam.reason)
        self.assertIn("热成像测温", cam.reason)

    def test_explosion_proof_reason_explains_capability(self):
        spec = build_spec("化工园区防爆区域监控，20路")
        cam = next(d for d in spec.devices if d.scene == "防爆区域")
        self.assertIn("能力命中 防爆", cam.reason)

    def test_generic_scene_reason_still_explained(self):
        spec = build_spec("某园区：周界20路，存储30天")
        cam = next(d for d in spec.devices if d.scene == "周界围墙")
        self.assertIn("周界围墙", cam.reason)
        self.assertNotIn("能力标签匹配", cam.reason)


if __name__ == "__main__":
    unittest.main()
