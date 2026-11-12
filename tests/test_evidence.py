"""判定链路与行业采纳的回归：证据透传、淘汰原因、LLM 行业兜底、弱线索追问。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import parser as P  # noqa: E402
from agent import subagents  # noqa: E402
from app.builder import analyze, match_scenes_detail  # noqa: E402
from app.taxonomy import INDUSTRY_LOW_SCORE  # noqa: E402


class IndustryEvidenceTest(unittest.TestCase):
    def test_evidence_exposed_on_plan(self):
        plan = analyze("某220kV变电站周界入侵，40路，存储30天")
        ev = plan.industry_evidence
        self.assertEqual(plan.industry, "智慧电力")
        self.assertFalse(ev["fallback"])
        self.assertGreater(ev["score"], 0)
        self.assertIn("变电站", ev["hits"])
        self.assertEqual(ev["ranked"][0]["industry"], "智慧电力")
        self.assertEqual(plan.to_dict()["industry_evidence"]["score"], ev["score"])

    def test_plan_roundtrip_keeps_evidence(self):
        from app.builder import Plan

        plan = analyze("某220kV变电站周界入侵，40路，存储30天")
        again = Plan.from_dict(plan.to_dict())
        self.assertEqual(again.industry_evidence, plan.industry_evidence)
        self.assertEqual(again.rejected_scenes, plan.rejected_scenes)


class RejectedSceneTest(unittest.TestCase):
    def test_suppressed_scene_has_reason(self):
        scenes, rejected = match_scenes_detail("某220kV变电站周界入侵")
        self.assertIn("变电站站区", [s["name"] for s in scenes])
        reasons = {r["scene"]: r["reason"] for r in rejected}
        self.assertIn("周界围墙", reasons)
        self.assertIn("压制", reasons["周界围墙"])

    def test_fallback_scene_without_lanes_is_rejected(self):
        scenes, rejected = match_scenes_detail("某园区：周界80路、出入口10路")
        names = [s["name"] for s in scenes]
        self.assertNotIn("公共区域", names)
        reasons = {r["scene"]: r["reason"] for r in rejected}
        self.assertIn("公共区域", reasons)
        self.assertIn("点位数", reasons["公共区域"])

    def test_foreign_industry_scene_is_rejected_with_reason(self):
        scenes, rejected = match_scenes_detail("学校实验室危化品监控", "智慧园区")
        names = [s["name"] for s in scenes]
        self.assertNotIn("实验室危化", names)
        reasons = {r["scene"]: r["reason"] for r in rejected}
        self.assertIn("智慧校园", reasons["实验室危化"])


class IndustryOverrideTest(unittest.TestCase):
    def test_override_applied_when_rule_has_no_hit(self):
        plan = analyze("某综合项目需要视频监控，50路，存储30天",
                       industry_override="智慧医疗")
        self.assertEqual(plan.industry, "智慧医疗")
        self.assertTrue(plan.industry_evidence["override"])
        self.assertTrue(any("大模型建议" in a for a in plan.assumptions))
        self.assertNotIn("需求未体现行业特征", "；".join(plan.gaps))

    def test_override_ignored_when_rule_hits(self):
        plan = analyze("某220kV变电站周界入侵，40路", industry_override="智慧医疗")
        self.assertEqual(plan.industry, "智慧电力")
        self.assertNotIn("override", plan.industry_evidence)

    def test_override_ignored_outside_whitelist(self):
        plan = analyze("某综合项目需要视频监控，50路", industry_override="火星行业")
        self.assertNotEqual(plan.industry, "火星行业")

    def test_weak_evidence_adds_confirm_gap(self):
        plan = analyze("某企业需要视频监控，50路，存储30天")
        self.assertEqual(plan.industry, "智慧园区")
        self.assertLess(plan.industry_evidence["score"], INDUSTRY_LOW_SCORE)
        self.assertTrue(any("行业线索较弱" in g for g in plan.gaps))


class ParserIndustryAdoptionTest(unittest.TestCase):
    def _parse(self, text, data):
        with patch.object(P.netcheck, "llm_reachable", return_value=True), \
             patch.object(P.C, "DEEPSEEK_API_KEY", "test-key"), \
             patch.object(P, "_json_from_llm", return_value=data):
            return P.parse_requirement(text)

    def test_adopts_llm_industry_when_rule_blind(self):
        r = self._parse("某综合项目需要视频监控，50路",
                        {"industry": "智慧医疗", "scenes": [], "project_name": None})
        self.assertEqual(r["industry_override"], "智慧医疗")
        self.assertTrue(any("已采纳" in w for w in r["warnings"]))

    def test_does_not_override_when_rule_hits(self):
        r = self._parse("某220kV变电站周界入侵，40路",
                        {"industry": "智慧医疗", "scenes": [], "project_name": None})
        self.assertIsNone(r["industry_override"])
        self.assertTrue(any("以原文为准" in w for w in r["warnings"]))

    def test_override_reaches_plan_via_run_intent(self):
        text = "某综合项目需要视频监控，50路，存储30天"

        def fake_parse(_text):
            return {"text": text, "engine": "llm", "scenes": [],
                    "industry_override": "智慧医疗",
                    "industry_suggestion": "智慧医疗", "warnings": []}

        out = subagents.run_intent({"text": text}, fake_parse)
        self.assertEqual(out["intent"]["industry"], "智慧医疗")
        self.assertEqual(out["parser"]["industry_override"], "智慧医疗")


if __name__ == "__main__":
    unittest.main()
