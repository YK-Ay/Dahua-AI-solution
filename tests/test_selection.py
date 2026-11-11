"""RAG 选型回归：场景蒸馏、真实案例证据、产品库硬校验、builder 接入。"""
import unittest

from app.builder import build_spec
from app.kb import ProductRepo
from app.selection import family_of, select_camera


class SceneFamilyTest(unittest.TestCase):
    def test_scene_category_mapping(self):
        self.assertEqual(family_of("小区大门-出入口"), "出入口")
        self.assertEqual(family_of("电梯轿厢-电瓶车入梯检测"), "电梯轿厢")
        self.assertEqual(family_of("周界围墙"), "周界围墙")
        self.assertEqual(family_of("枪机产品选型"), "")


class SelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = ProductRepo()
        cls.allowed = cls.repo.allowed_models()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def test_elevator_picks_real_elevator_camera(self):
        """电梯场景应学到真实配单里的「梯控相机」，而不是通用 400 万半球。"""
        sel = select_camera(
            self.repo, "电梯轿厢", intent="电瓶车入梯检测",
            industries=["小区社区"], classes=["网络摄像机"],
            keywords=["半球", "400万"],
        )
        self.assertIsNotNone(sel)
        self.assertIn("电梯", sel["product"]["display_name"])
        self.assertGreaterEqual(sel["refs"], 2)
        self.assertTrue(sel["evidence"])
        self.assertEqual(sel["engine"]["family"], "电梯轿厢")

    def test_selected_model_always_in_product_db(self):
        scenes = [
            ("周界围墙", "入侵检测", ["工厂园区"]),
            ("出入口", "人车通行管理", ["小区社区"]),
            ("室内区域", "人员活动监控", ["智慧校园"]),
            ("公共区域", "公共区域视频监控", ["工厂园区"]),
        ]
        for scene, intent, industries in scenes:
            with self.subTest(scene=scene):
                sel = select_camera(self.repo, scene, intent, industries,
                                    classes=["网络摄像机"])
                self.assertIsNotNone(sel)
                self.assertIn(sel["product"]["model"], self.allowed)

    def test_entrance_excludes_unrelated_special_cameras(self):
        sel = select_camera(
            self.repo, "出入口", intent="人车通行管理",
            industries=["小区社区"], classes=["网络摄像机"],
            keywords=["枪型", "400万"],
        )
        self.assertIsNotNone(sel)
        name = sel["product"]["display_name"]
        for noise in ("客流", "火焰", "通道占用", "高空抛物"):
            self.assertNotIn(noise, name)

    def test_unknown_scene_returns_none(self):
        self.assertIsNone(select_camera(self.repo, "收银台", industries=["中小商铺"]))


class BuilderIntegrationTest(unittest.TestCase):
    def test_camera_reason_carries_rag_evidence(self):
        spec = build_spec("某小区：园区周界20路、单元楼电梯16路，存储30天")
        cams = [d for d in spec.devices if d.category == "网络摄像机"]
        self.assertGreaterEqual(len(cams), 2)
        self.assertTrue(all("RAG 参考" in d.reason for d in cams))
        elevator = next(d for d in cams if d.scene == "电梯轿厢")
        self.assertIn("电梯", elevator.display_name)

    def test_every_device_still_priced_or_flagged(self):
        """RAG 只换型号，不改变既有报价口径。"""
        spec = build_spec("某园区：周界80路、出入口10路，存储30天")
        self.assertTrue(all(d.model for d in spec.devices))
        self.assertTrue(all(d.qty > 0 for d in spec.devices))


if __name__ == "__main__":
    unittest.main()
