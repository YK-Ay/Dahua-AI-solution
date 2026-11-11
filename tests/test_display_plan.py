"""显示层分级（监视器 / 电视墙）与拼接屏待询价的回归测试。"""
import unittest

from app.builder import build_spec, display_plan
from app.quote import build_quote


class DisplayPlanTest(unittest.TestCase):
    def test_tier_boundaries(self):
        self.assertEqual(display_plan(1), (1, 1))
        self.assertEqual(display_plan(16), (1, 1))
        self.assertEqual(display_plan(17), (2, 2))
        self.assertEqual(display_plan(48), (2, 2))
        self.assertEqual(display_plan(49), (3, 3))
        self.assertEqual(display_plan(128), (3, 3))
        self.assertEqual(display_plan(129), (4, 3))

    def test_small_project_uses_single_55_inch_monitor(self):
        spec = build_spec("某商铺，收银台6路、门口4路，存储30天")
        screens = [d for d in spec.devices if d.category == "监视器"]
        self.assertEqual(len(screens), 1)
        self.assertEqual(screens[0].qty, 1)
        self.assertIn("55", screens[0].display_name)

    def test_medium_project_gets_wall_bracket_and_decoder(self):
        spec = build_spec("某园区，周界20路、出入口8路、电梯10路，存储30天")
        wall = [d for d in spec.devices if d.category == "拼接屏"]
        self.assertEqual(len(wall), 1)
        self.assertEqual(wall[0].qty, 4)
        self.assertIsNone(wall[0].unit_price)  # 产品库无标准价
        wall_cats = [d.category for d in spec.devices]
        self.assertIn("拼接支架", wall_cats)
        self.assertIn("解码器", wall_cats)
        self.assertFalse(any(d.category == "监视器" for d in spec.devices))

        quote = build_quote(spec)
        self.assertEqual(quote["unpriced"], 1)
        priced = sum(line["amount"] for line in quote["lines"]
                     if line["amount"] is not None)
        self.assertAlmostEqual(quote["total"], priced)


if __name__ == "__main__":
    unittest.main()
