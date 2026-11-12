"""平台层分层选型：SMB 一体机 / ICC 纯软 + 通道授权 + 服务器。"""
import unittest

from app.builder import build_spec, parse_lane_total
from app.kb import ProductRepo


def platform_devices(spec):
    return [d for d in spec.devices if "平台" in d.category]


def camera_lanes(spec):
    return sum(d.qty for d in spec.devices if d.category == "网络摄像机")


class PlatformTierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = ProductRepo()
        cls.allowed = cls.repo.allowed_models()

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()

    def test_small_project_uses_smb_h2(self):
        spec = build_spec("某商铺：门口4路、收银台6路，存储30天", repo=self.repo)
        devs = platform_devices(spec)
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0].model, "DH-SMB-H2")
        self.assertEqual(devs[0].qty, 1)
        self.assertEqual(devs[0].unit_price, 29000)

    def test_medium_project_uses_smb_x1(self):
        spec = build_spec(
            "某小区：周界20路、出入口10路、电梯10路、地库10路，存储30天",
            repo=self.repo,
        )
        devs = platform_devices(spec)
        self.assertEqual([d.model for d in devs], ["DH-SMB-X1"])
        self.assertEqual(devs[0].unit_price, 76000)

    def test_large_project_splits_icc_into_three_items(self):
        spec = build_spec(
            "某园区：周界80路、出入口20路、停车场40路、室内60路，存储30天",
            repo=self.repo,
        )
        devs = {d.category: d for d in platform_devices(spec)}
        self.assertEqual(set(devs), {"综合平台", "平台授权", "平台服务器"})
        self.assertEqual(devs["综合平台"].model, "DH-ICC-B8900-U-PRO")
        self.assertIsNone(devs["综合平台"].unit_price)  # 纯软无标准价，待询价
        self.assertEqual(devs["平台授权"].model, "DH-ICC-Common-VD-COCHN")
        self.assertEqual(devs["平台授权"].qty, camera_lanes(spec))
        self.assertEqual(devs["平台授权"].unit_price, 100)
        self.assertEqual(devs["平台服务器"].model, "DH-ICC-B8900S3H-HW-ARM-U64")
        self.assertIsNone(devs["平台服务器"].unit_price)
        self.assertIn("中标价", devs["综合平台"].reason)

    def test_traffic_project_uses_icc_even_when_small(self):
        spec = build_spec("某县交警：主干道卡口30路，存储90天", repo=self.repo)
        models = [d.model for d in platform_devices(spec)]
        self.assertIn("DH-ICC-B8900-U-PRO", models)
        self.assertTrue(any("交管" in d.reason for d in platform_devices(spec)))

    def test_over_256_lanes_uses_flagship_server(self):
        spec = build_spec(
            "某园区：周界120路、出入口100路、室内80路，存储30天", repo=self.repo
        )
        servers = [d for d in platform_devices(spec) if d.category == "平台服务器"]
        self.assertEqual(servers[0].model, "DH-ICC-B8900S4H-HW-ARM-U128")

    def test_all_platform_models_exist_in_product_db(self):
        for text in (
            "某商铺：门口4路，存储30天",
            "某小区：周界20路、出入口10路、电梯10路、地库10路，存储30天",
            "某园区：周界120路、出入口100路、室内80路，存储30天",
        ):
            spec = build_spec(text, repo=self.repo)
            for d in platform_devices(spec):
                self.assertIn(d.model, self.allowed)


    def test_same_prompt_keeps_platform_stable(self):
        text = "某园区：周界80路、出入口20路、停车场40路、室内60路，存储30天"
        models = [
            tuple(d.model for d in platform_devices(build_spec(text, repo=self.repo)))
            for _ in range(5)
        ]
        self.assertEqual(len(set(models)), 1)
        self.assertIn("DH-ICC-B8900-U-PRO", models[0])


class LaneTotalTest(unittest.TestCase):
    def test_sums_scene_lanes_without_total_marker(self):
        self.assertEqual(parse_lane_total("周界80路，出入口20路"), 100)

    def test_prefers_explicit_total_marker(self):
        self.assertEqual(parse_lane_total("共170路，周界80路，出入口20路"), 170)

    def test_prefers_breakdown_marker(self):
        self.assertEqual(parse_lane_total("170路，其中周界80路、出入口20路"), 170)

    def test_unmatched_scene_lanes_are_not_lost(self):
        self.assertEqual(parse_lane_total("周界80路，出入口20路，主路30路"), 130)

    def test_device_spec_is_not_counted_as_lane(self):
        self.assertEqual(parse_lane_total("周界80路，出入口20路，配16路NVR"), 100)

    def test_no_lane_number(self):
        self.assertIsNone(parse_lane_total("某园区门禁与监控需求"))


if __name__ == "__main__":
    unittest.main()
