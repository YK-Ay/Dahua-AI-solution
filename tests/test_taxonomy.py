"""行业 → 场景 → 能力 → 设备族 分类体系回归。

覆盖四层：
1. 数据完整性：行业/场景表自洽，场景引用的设备类目在库里真实存在；
2. 行业判定：12 个行业都能被代表性需求命中，且不会全部塌陷成园区；
3. 场景识别：行业限定、场景压制、兜底场景与路数归属；
4. 选型联动：专属设备被真正选中，缺货时按场景开关报缺或降级。
"""
import sqlite3
import unittest

from app.builder import SpecGateError, build_spec, detect_industry, match_scenes
from app.builder import explicit_lanes
from app.taxonomy import (
    FALLBACK_SCENE,
    GENERIC_CAMERA_CLASSES,
    INDUSTRIES,
    SCENES,
    scene,
    industry_of,
)
from config.settings import DB_PATH

# 每个行业给 3 条代表性需求，覆盖别名与常见口语
INDUSTRY_CASES = {
    "智慧电力": ["某220kV变电站周界入侵", "输电线路杆塔防外破", "配电房开关柜巡检",
             "光伏电站周界", "储能站电池舱监控"],
    "智慧交管": ["高速公路卡口测速", "城市道路电子警察", "违停抓拍",
             "路口信号机联网", "车路协同示范段"],
    "智慧化工能源": ["化工园区防爆区域", "加油站便利店", "油库罐区监控", "燃气门站"],
    "智慧水利": ["水库大坝水位监测", "河道防溺水", "泵站闸站监控", "防汛应急指挥"],
    "智慧金融": ["银行网点现金区", "ATM自助银行", "金库双人双锁"],
    "智慧医疗": ["医院门诊大厅", "手术室ICU", "乡镇卫生院"],
    "文博景区": ["博物馆文物库房", "景区游客中心", "纪念馆展陈"],
    "零售连锁": ["连锁便利店收银", "超市卖场", "快递驿站"],
    "智慧校园": ["某中学教学楼", "小学校区操场", "大学图书馆"],
    "智慧社区": ["某小区地下车库", "安置房周界", "社区服务中心"],
    "智慧楼宇": ["写字楼大堂", "酒店客房层", "商业综合体"],
    "智慧园区": ["工业园区周界", "产业园出入口", "物流园仓库"],
}

# 每个行业的专属场景：必须在对应行业下命中，且不得出现在其它行业
EXCLUSIVE_SCENES = {
    "输电线路监测": "输电线路杆塔防外破",
    "变电设备测温": "变电站设备测温",
    "道路卡口": "卡口过车抓拍",
    "电子警察": "电子警察违法抓拍",
    "防爆区域": "化工防爆区域",
    "气体泄漏监测": "可燃气体泄漏",
    "水库河道": "水库河道水位",
    "泵站闸站": "泵站闸站",
    "金库": "银行金库",
    "门诊大厅": "医院门诊",
    "手术室ICU": "医院手术室",
    "文物库房": "博物馆文物",
    "山林防火": "山林防火",
    "门店收银": "连锁门店收银",
    "实验室危化": "学校实验室危化品",
}


def scene_names(text, industry=None):
    return [r["name"] for r in match_scenes(text, industry)]


class TaxonomyDataTest(unittest.TestCase):
    """分类表自身的自洽性——改表时最容易出错的地方都在这里兜住。"""

    def test_scene_names_unique(self):
        names = [s["name"] for s in SCENES]
        self.assertEqual(len(names), len(set(names)))

    def test_scene_has_keys_and_intent(self):
        for s in SCENES:
            self.assertTrue(s["keys"], s["name"])
            self.assertTrue(s["intent"], s["name"])
            self.assertTrue(s["camera_classes"], s["name"])

    def test_suppress_targets_exist(self):
        names = {s["name"] for s in SCENES}
        for s in SCENES:
            for target in s.get("suppresses", []):
                self.assertIn(target, names, f"{s['name']} 压制了不存在的场景 {target}")

    def test_rag_family_exists(self):
        names = {s["name"] for s in SCENES}
        for s in SCENES:
            family = s.get("rag_family")
            if family:
                self.assertIn(family, names, f"{s['name']} 的 rag_family 不存在")

    def test_scene_industries_declared(self):
        for s in SCENES:
            for name in s.get("industries", []):
                self.assertIn(name, INDUSTRIES, f"{s['name']} 声明了未知行业 {name}")

    def test_fallback_scene_is_last_resort(self):
        names = [s["name"] for s in SCENES]
        self.assertIn(FALLBACK_SCENE, names)
        self.assertTrue(scene(FALLBACK_SCENE)["fallback_only_with_lanes"])

    def test_camera_classes_exist_in_product_db(self):
        conn = sqlite3.connect(str(DB_PATH))
        try:
            rows = conn.execute("select distinct class_name from products").fetchall()
        finally:
            conn.close()
        known = {r[0] for r in rows}
        for s in SCENES:
            for cls in s["camera_classes"]:
                self.assertIn(cls, known, f"{s['name']} 引用了库里没有的类目 {cls}")

    def test_degrade_values_are_valid(self):
        for s in SCENES:
            self.assertIn(scene(s["name"])["degrade"], ("fallback", "report"))

    def test_every_industry_has_keys_and_boms(self):
        for name, spec in INDUSTRIES.items():
            self.assertTrue(spec["keys"], name)
            self.assertTrue(spec["bom_industries"], name)


class IndustryDetectTest(unittest.TestCase):
    def test_all_twelve_industries(self):
        for industry, texts in INDUSTRY_CASES.items():
            for text in texts:
                self.assertEqual(detect_industry(text)[0], industry, text)

    def test_twelve_industries(self):
        self.assertEqual(len(INDUSTRIES), 12)
        self.assertEqual(len(INDUSTRY_CASES), 12)

    def test_power_not_collapsed_into_park(self):
        self.assertEqual(detect_industry("化工园区防爆区域")[0], "智慧化工能源")
        self.assertEqual(detect_industry("某220kV变电站")[0], "智慧电力")

    def test_unknown_industry_is_marked(self):
        industry, info = industry_of("某项目需要装一些监控")
        self.assertTrue(info["fallback"])
        self.assertEqual(info["score"], 0)
        self.assertEqual(industry, "智慧园区")

    def test_scores_are_exposed(self):
        _, info = industry_of("城市道路电子警察和违停抓拍")
        self.assertFalse(info["fallback"])
        self.assertGreater(info["score"], 0)
        self.assertEqual(info["ranked"][0]["industry"], "智慧交管")
        self.assertIn("电子警察", info["hits"])

    def test_community_tag(self):
        self.assertIn("小区社区", detect_industry("某小区周界")[1])
        self.assertEqual(detect_industry("某工业园区周界")[1], [])


class SceneDetectTest(unittest.TestCase):
    def test_scene_names_are_stable(self):
        names = scene_names("某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路")
        self.assertIn("周界围墙", names)
        self.assertIn("出入口", names)
        self.assertIn("室内区域", names)

    def test_power_scene_suppresses_generic_perimeter(self):
        names = scene_names("某220kV变电站周界入侵")
        self.assertIn("变电站站区", names)
        self.assertNotIn("周界围墙", names)

    def test_traffic_scene_suppresses_road(self):
        names = scene_names("城市道路电子警察抓拍")
        self.assertIn("电子警察", names)
        self.assertNotIn("主干道", names)
        self.assertNotIn("道路卡口", names)

    def test_parking_entrance_suppresses_parking_lot(self):
        names = scene_names("停车场出入口道闸和车牌识别")
        self.assertIn("停车出入口", names)
        self.assertNotIn("停车场", names)

    def test_pedestrian_gate_suppresses_generic_entrance(self):
        names = scene_names("人行道闸和访客通道")
        self.assertIn("人行闸机", names)
        self.assertNotIn("出入口", names)

    def test_power_distribution_room_suppresses_generic_room(self):
        names = scene_names("配电房和机房动环监控")
        self.assertIn("配电房", names)
        self.assertNotIn("室内区域", names)
        self.assertNotIn("变电设备测温", names)

    def test_temperature_scene_when_no_room_mentioned(self):
        names = scene_names("变电站设备测温")
        self.assertIn("变电设备测温", names)

    def test_fallback_scene_when_nothing_matched(self):
        self.assertEqual(scene_names("随便装几个摄像头"), [FALLBACK_SCENE])

    def test_fallback_scene_dropped_without_lanes(self):
        # 「园区」只是地点修饰，没有给公共区域点位数时不该抢路数
        names = scene_names("某园区：周界80路、出入口10路")
        self.assertNotIn(FALLBACK_SCENE, names)

    def test_fallback_scene_kept_with_explicit_lanes(self):
        names = scene_names("某园区：周界80路、公共区域40路")
        self.assertIn(FALLBACK_SCENE, names)

    def test_exclusive_scenes_are_industry_scoped(self):
        for scene_name, text in EXCLUSIVE_SCENES.items():
            owners = scene(scene_name)["industries"]
            if not owners:
                continue
            self.assertIn(scene_name, scene_names(text, owners[0]), scene_name)
            for other in INDUSTRIES:
                if other in owners:
                    continue
                self.assertNotIn(scene_name, scene_names(text, other),
                                 f"{scene_name} 泄漏到 {other}")

    def test_school_lab_only_in_campus(self):
        self.assertIn("实验室危化", scene_names("学校实验室", "智慧校园"))
        self.assertNotIn("实验室危化", scene_names("学校实验室", "智慧园区"))


class LaneAllocationTest(unittest.TestCase):
    def test_new_scene_lanes_are_attributed(self):
        text = "某220kV变电站安防项目：站区周界40路，配电房测温10路，输电线路通道20路"
        names = scene_names(text)
        alloc = explicit_lanes(text, [scene(n) for n in names])
        self.assertEqual(alloc.get("变电站站区"), 40)
        self.assertEqual(alloc.get("配电房"), 10)
        self.assertEqual(alloc.get("输电线路监测"), 20)

    def test_traffic_lanes_are_attributed(self):
        text = "卡口测速抓拍20路，电子警察15路，违停抓拍10路"
        alloc = explicit_lanes(text, [scene(n) for n in scene_names(text)])
        self.assertEqual(alloc.get("测速卡口"), 20)
        self.assertEqual(alloc.get("电子警察"), 15)
        self.assertEqual(alloc.get("违停抓拍"), 10)

    def test_unmatched_text_falls_back_to_default_lanes(self):
        from app.builder import analyze

        plan = analyze("随便装几个摄像头")
        self.assertEqual([r.target for r in plan.requirements], [FALLBACK_SCENE])


class ExclusiveDeviceTest(unittest.TestCase):
    def test_transmission_line_uses_transmission_camera(self):
        spec = build_spec("输电线路杆塔在线监测，防外破，30个点位")
        cam = spec.devices[0]
        self.assertEqual(cam.scene, "输电线路监测")
        self.assertEqual(cam.category, "输电监测相机")
        self.assertIn("DH-TLM", cam.model)

    def test_traffic_junction_uses_capture_unit(self):
        spec = build_spec("高速公路卡口测速抓拍，20路，保存90天")
        cam = spec.devices[0]
        self.assertEqual(cam.scene, "测速卡口")
        self.assertEqual(cam.category, "测速抓拍单元")
        self.assertIn("YG-L", cam.model)

    def test_explosion_proof_area_uses_explosion_proof_camera(self):
        spec = build_spec("化工园区防爆区域监控，80路")
        cam = spec.devices[0]
        self.assertEqual(cam.scene, "防爆区域")
        self.assertEqual(cam.category, "防爆摄像机")
        self.assertEqual(cam.qty, 80)

    def test_missing_exclusive_device_reports_gap(self):
        for text in ("博物馆文物库房，15路", "医院手术室和ICU，10路", "银行金库，8路"):
            with self.assertRaises(SpecGateError, msg=text) as ctx:
                build_spec(text)
            self.assertIn("人工确认型号", str(ctx.exception))

    def test_degrade_switch_is_per_scene(self):
        self.assertEqual(scene("文物库房")["degrade"], "report")
        self.assertTrue(scene("文物库房")["exclusive"])
        self.assertEqual(scene("变电站站区")["degrade"], "fallback")
        self.assertFalse(scene("变电站站区")["exclusive"])

    def test_generic_scene_still_uses_rag_or_library(self):
        spec = build_spec("某园区：周界80路、出入口10路，存储30天")
        cams = [d for d in spec.devices if d.scene]
        self.assertTrue(cams)
        for cam in cams:
            self.assertTrue(cam.reason, cam.model)

    def test_generic_classes_exist_in_product_db(self):
        conn = sqlite3.connect(str(DB_PATH))
        try:
            known = {r[0] for r in conn.execute("select distinct class_name from products")}
        finally:
            conn.close()
        for cls in GENERIC_CAMERA_CLASSES:
            self.assertIn(cls, known, cls)


class ParserWhitelistTest(unittest.TestCase):
    def test_llm_whitelist_covers_new_industries_and_scenes(self):
        from agent import parser

        self.assertEqual(len(parser.INDUSTRIES), 12)
        self.assertIn("智慧电力", parser.INDUSTRIES)
        self.assertIn("输电线路监测", parser.SCENE_NAMES)
        self.assertIn("道路卡口", parser.SCENE_NAMES)
        self.assertEqual(parser.FALLBACK_SCENE, FALLBACK_SCENE)


if __name__ == "__main__":
    unittest.main()
