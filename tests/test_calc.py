"""存储/带宽计算单测：数字必须能手工验算。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.builder import SpecGateError, validate_spec
from app.calc import calc_bandwidth, calc_storage, gb_per_day_per_lane, total_lanes
from app.models import Device, Network, Project, Requirement, Spec, Storage


class CalcTest(unittest.TestCase):
    def test_gb_per_day_per_lane(self):
        # 4 Mbps × 86400 / 8 / 1024 = 42.1875 GB/天/路
        self.assertAlmostEqual(gb_per_day_per_lane(4), 42.1875, places=4)

    def test_storage_matches_framework_example(self):
        # 框架 v1.1 示例：100 路 × 4 Mbps × 30 天 → 123.6 TB，冗余 136.0 TB
        reqs = [Requirement(target="周界", intent="入侵检测", lanes=100, bitrate_mbps=4)]
        s = calc_storage(reqs, 30)
        self.assertEqual(s["raw_tb"], 123.6)
        self.assertEqual(s["with_redundancy_tb"], 136.0)

    def test_storage_mixed_scenes(self):
        reqs = [
            Requirement(target="周界", intent="入侵检测", lanes=80, bitrate_mbps=4),
            Requirement(target="出入口", intent="人车通行", lanes=20, bitrate_mbps=6),
        ]
        s = calc_storage(reqs, 30)
        # Σ 码流 = 80×4 + 20×6 = 440 Mbps
        self.assertAlmostEqual(gb_per_day_per_lane(440) * 30 / 1024, s["raw_tb"], places=1)
        self.assertEqual(total_lanes(reqs), 100)

    def test_bandwidth(self):
        reqs = [Requirement(target="周界", intent="入侵检测", lanes=100, bitrate_mbps=4)]
        self.assertEqual(calc_bandwidth(reqs), 480.0)

    def test_invalid_days(self):
        with self.assertRaises(ValueError):
            calc_storage([], 0)

    def test_gate_rejects_unknown_model(self):
        spec = Spec(
            project=Project(name="测试", industry="智慧园区"),
            requirements=[],
            devices=[Device(category="NVR", model="DH-FAKE-999", qty=1)],
            storage=Storage(retention_days=30, raw_tb=10, with_redundancy_tb=11),
            network=Network(uplink_mbps=100),
        )
        with self.assertRaises(SpecGateError):
            validate_spec(spec, allowed_models={"DH-NVR4432-HDS3/I"})


if __name__ == "__main__":
    unittest.main()
