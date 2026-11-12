"""LLM 语义解析与缺口追问改写的回归：归一化生效、数字零产出、离线降级。"""
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import graph as G  # noqa: E402
from agent import parser as P  # noqa: E402
from app.builder import SpecGateError, match_scenes  # noqa: E402

ORAL = "某小区地库要看清车牌，档案室和楼梯间也要装监控，存储30天"
DETAILED = "某园区：周界20路，出入口10路，存储30天。"
NUM = re.compile(r"\d+")


class OnlineMixin:
    """统一把解析层切到「有网 + 有 Key」，但绝不真的发请求。"""

    def online(self):
        return [
            patch.object(P.netcheck, "llm_reachable", return_value=True),
            patch.object(P.C, "DEEPSEEK_API_KEY", "test-key"),
            patch.object(P, "_json_from_llm", return_value={}),
        ]


class NormalizeTest(OnlineMixin, unittest.TestCase):
    def setUp(self):
        P.clear_gap_cache()

    def _parse(self, data, text=ORAL):
        ps = self.online()
        ps[-1] = patch.object(P, "_json_from_llm", return_value=data)
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        return P.parse_requirement(text)

    def test_oral_scenes_are_written_back(self):
        r = self._parse({"scenes": ["停车场", "室内区域"],
                         "project_name": "某小区监控项目", "industry": "智慧园区"})
        self.assertEqual(r["engine"], "llm")
        # 「地库」规则引擎已认识，「档案室/楼梯间」要靠 LLM 补写成室内区域
        self.assertEqual(r["normalized_scenes"], ["室内区域"])
        self.assertIn("识别场景：室内区域", r["text"])
        # 补写内容绝不能带数字，原文数字保持原样
        appended = r["text"].split("识别场景：")[-1]
        self.assertEqual(NUM.findall(appended), [])
        self.assertEqual(NUM.findall(r["text"]), NUM.findall(ORAL))
        # 前端看到的 scenes 是补写后真正生效的，而不是 LLM 自报的
        self.assertEqual(r["scenes"], [x["name"] for x in match_scenes(r["text"])])
        self.assertIn("停车场", r["scenes"])
        self.assertIn("室内区域", r["scenes"])

    def test_scene_whitelist_and_digit_recheck(self):
        r = self._parse({"scenes": ["室内区域", "火星基地", "99号区域"],
                         "project_name": "某小区99号楼", "industry": "智慧交管"})
        self.assertEqual(r["normalized_scenes"], ["室内区域"])
        self.assertIn("火星基地", r["dropped_scenes"])
        self.assertIsNone(r["project_name"])          # 含原文没有的数字，整个字段作废
        self.assertNotIn("智慧交管", r["text"])        # 行业只提示，不写回原文
        self.assertEqual(r["industry_suggestion"], "智慧交管")
        self.assertIsNone(r["industry_override"])      # 规则已命中，不采纳 LLM 行业
        self.assertTrue(any("以原文为准" in w for w in r["warnings"]))

    def test_no_overwrite_when_rules_already_confident(self):
        # 规则引擎已认出 2 个明确场景，LLM 再补的「停车场」视为臆测，不采纳
        r = self._parse({"scenes": ["周界围墙", "出入口", "停车场"]}, text=DETAILED)
        self.assertEqual(r["text"], DETAILED)
        self.assertEqual(r["normalized_scenes"], [])
        self.assertTrue(any("未采纳" in w for w in r["warnings"]))

    def test_offline_never_calls_network(self):
        with patch.object(P.netcheck, "llm_reachable", return_value=False), \
             patch.object(P.requests, "post", side_effect=AssertionError("不应联网")) as post:
            r = P.parse_requirement(ORAL)
            post.assert_not_called()
        self.assertEqual(r["engine"], "offline")
        self.assertEqual(r["text"], ORAL)

    def test_llm_failure_falls_back_to_rules(self):
        ps = self.online()
        ps[-1] = patch.object(P, "_json_from_llm", side_effect=TimeoutError("timeout"))
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        r = P.parse_requirement(ORAL)
        self.assertEqual(r["text"], ORAL)
        self.assertTrue(any("回退规则引擎" in w for w in r["warnings"]))


class CnNumberTest(unittest.TestCase):
    def test_local_conversion_never_invents_numbers(self):
        t = "园区里大概一百个点位，录像留三十天，一期先做主干道"
        out, changes = P.normalize_cn_numbers(t)
        self.assertIn("100个点位", out)
        self.assertIn("30天", out)
        self.assertIn("一期", out)                     # 非数量词不误伤
        self.assertEqual(len(changes), 2)
        self.assertEqual(P.normalize_cn_numbers(out)[0], out)   # 幂等

    def test_offline_still_normalizes_and_feeds_build_spec(self):
        from app.builder import build_spec
        t = "某园区主干道大概一百个点位，录像留三十天"
        with patch.object(P.netcheck, "llm_reachable", return_value=False), \
             patch.object(P.requests, "post", side_effect=AssertionError("不应联网")):
            r = P.parse_requirement(t)
        self.assertEqual(r["engine"], "offline")
        self.assertTrue(r["number_normalized"])
        spec = build_spec(r["text"])
        self.assertEqual(sum(x.lanes for x in spec.requirements), 100)
        self.assertEqual(spec.storage.retention_days, 30)


class SceneCoverageTest(unittest.TestCase):
    """场景词覆盖与路数归属的回归：教学楼/食堂/车库/操场此前会被丢掉。"""

    def test_school_scenes_and_lanes(self):
        from app.builder import build_spec
        spec = build_spec("某学校项目：教学楼60路，食堂15路，操场40路，存储30天")
        got = {r.target: r.lanes for r in spec.requirements}
        self.assertEqual(got.get("室内区域"), 75)      # 教学楼 60 + 食堂 15
        self.assertEqual(got.get("公共区域"), 40)      # 操场
        self.assertEqual(sum(got.values()), 115)

    def test_garage_maps_to_parking(self):
        from app.builder import build_spec
        spec = build_spec("某小区地下车库要看清车牌，电梯里也要装监控，存储30天")
        self.assertIn("停车场", [r.target for r in spec.requirements])

    def test_public_area_kept_only_with_explicit_lanes(self):
        from app.builder import build_spec
        with_lanes = build_spec("某园区：周界80路，公共区域40路，存储30天")
        self.assertIn("公共区域", [r.target for r in with_lanes.requirements])
        without = build_spec("某园区：周界80路，存储30天")
        self.assertNotIn("公共区域", [r.target for r in without.requirements])

    def test_same_scene_lanes_are_summed(self):
        from app.builder import build_spec
        spec = build_spec("某园区办公楼：会议室20路，走廊30路，大厅20路，存储90天")
        got = {r.target: r.lanes for r in spec.requirements}
        self.assertEqual(got.get("室内区域"), 70)


class GapPhraseTest(unittest.TestCase):
    def setUp(self):
        P.clear_gap_cache()
        self.gaps = [
            "需求未写点位数，按默认 50 路估算（可通过 --lanes 调整）",
            "需求未写存储天数，按默认 30 天测算（可通过 --days 调整）",
        ]
        self.online_patches = [
            patch.object(P.netcheck, "llm_reachable", return_value=True),
            patch.object(P.C, "DEEPSEEK_API_KEY", "test-key"),
        ]
        for p in self.online_patches:
            p.start()
            self.addCleanup(p.stop)

    def _phrase(self, data):
        with patch.object(P, "_json_for_gaps", return_value=data) as call:
            r = P.phrase_gaps(self.gaps)
        return r, call

    def test_llm_rewrites_questions_without_digits(self):
        r, _ = self._phrase({"questions": [
            "请给出项目一共需要多少个摄像机点位",
            "请明确录像需要保存多少天",
        ]})
        self.assertEqual(r["engine"], "llm")
        self.assertEqual(len(r["items"]), 2)
        self.assertEqual([x["gap"] for x in r["items"]], self.gaps)
        for item in r["items"]:
            self.assertEqual(NUM.findall(item["question"]), [])

    def test_any_digit_rejects_whole_batch(self):
        r, _ = self._phrase({"questions": ["请确认是否需要 100 个点位", "请明确存储天数"]})
        self.assertEqual(r["engine"], "rule")
        self.assertEqual(len(r["items"]), 2)
        self.assertTrue(any("回退本地模板" in w for w in r["warnings"]))
        for item in r["items"]:
            self.assertEqual(NUM.findall(item["question"]), [])

    def test_count_mismatch_rejects_whole_batch(self):
        r, _ = self._phrase({"questions": ["只有一条追问"]})
        self.assertEqual(r["engine"], "rule")
        self.assertEqual(len(r["items"]), 2)

    def test_cached_result_avoids_second_llm_call(self):
        P.clear_gap_cache()
        with patch.object(P, "_json_for_gaps", return_value={"questions": [
            "请给出项目一共需要多少个摄像机点位", "请明确录像需要保存多少天",
        ]}) as call:
            P.phrase_gaps(self.gaps)
            P.phrase_gaps(self.gaps)
        self.assertEqual(call.call_count, 1)

    def test_offline_uses_local_template_without_network(self):
        with patch.object(P.netcheck, "llm_reachable", return_value=False), \
             patch.object(P.requests, "post", side_effect=AssertionError("不应联网")) as post:
            r = P.phrase_gaps(self.gaps)
            post.assert_not_called()
        self.assertEqual(r["engine"], "offline")
        self.assertEqual(len(r["items"]), 2)
        for item in r["items"]:
            self.assertEqual(NUM.findall(item["question"]), [])


class GapInterruptTest(unittest.TestCase):
    """图节点中断时，前端能拿到逐条追问（离线走本地模板）。"""

    def test_interrupt_payload_has_questions(self):
        import tempfile
        from agent import service as S
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        graph = G.build_graph(Path(tmp.name) / "ckpt.sqlite")
        self.addCleanup(graph.checkpointer.conn.close)
        real_build = G.build_spec
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SpecGateError(["主干道点位数未确定", "存储天数未明确"])
            return real_build(*args, **kwargs)

        with patch.object(G, "build_spec", flaky), \
             patch.object(S, "get_graph", lambda: graph), \
             patch.object(P.netcheck, "llm_reachable", return_value=False):
            d = S.start(DETAILED)
        self.assertEqual(d["interrupt_type"], "gap")
        ip = d["interrupt"]
        self.assertEqual(len(ip["questions"]), 2)
        self.assertEqual(ip["question_engine"], "offline")
        self.assertTrue(all(x["question"] for x in ip["questions"]))


if __name__ == "__main__":
    unittest.main()
