"""Agent 工具层回归：确定性工具结果、离线降级、LLM 工具调用循环（mock）。"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import tools as T  # noqa: E402
from app.compare import REPRESENTATIVES  # noqa: E402


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    return r


class RunToolTest(unittest.TestCase):
    def test_calc_storage_deterministic(self):
        out = T.run_tool("calc_storage", {"lanes": 100, "bitrate_mbps": 4,
                                          "retention_days": 30})
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["raw_tb"], 123.6)
        self.assertEqual(out["result"]["with_redundancy_tb"], 136.0)

    def test_calc_bandwidth_deterministic(self):
        out = T.run_tool("calc_bandwidth", {"lanes": 170, "bitrate_mbps": 4})
        self.assertTrue(out["ok"])
        # 170 × 4 × 1.2 = 816.0
        self.assertEqual(out["result"]["uplink_mbps"], 816.0)

    def test_invalid_numbers_rejected(self):
        for args in ({"lanes": 0, "bitrate_mbps": 4, "retention_days": 30},
                     {"lanes": "abc", "bitrate_mbps": 4, "retention_days": 30},
                     {"lanes": 10, "bitrate_mbps": -1, "retention_days": 30}):
            out = T.run_tool("calc_storage", args)
            self.assertFalse(out["ok"], args)

    def test_unknown_tool_and_bad_args(self):
        self.assertFalse(T.run_tool("nope", {})["ok"])
        self.assertFalse(T.run_tool("search_kb", None)["ok"])
        # search_kb 缺关键词 → error 结构，ok=False
        self.assertFalse(T.run_tool("search_kb", {})["ok"])

    def test_search_kb_hits_real_db(self):
        out = T.run_tool("search_kb", {"keyword": "DH-IPC", "limit": 5})
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["result"]["found"], 1)
        self.assertTrue(all(str(x["model"]).startswith("DH-")
                            for x in out["result"]["items"]))

    def test_compare_pair_official_models(self):
        d_model, h_model = REPRESENTATIVES["PoE交换机"]
        out = T.run_tool("compare_pair", {
            "category": "PoE交换机", "dahua_model": d_model, "hik_model": h_model})
        self.assertTrue(out["ok"], out)
        res = out["result"]
        self.assertEqual(res["category"], "PoE交换机")
        self.assertGreater(res["coverage"]["aligned"], 0)
        self.assertEqual(len(res["rows"]), res["coverage"]["total"])

    def test_compare_pair_bad_category(self):
        d_model, h_model = REPRESENTATIVES["NVR"]
        out = T.run_tool("compare_pair", {
            "category": "打印机", "dahua_model": d_model, "hik_model": h_model})
        self.assertFalse(out["ok"])


class ResearchTest(unittest.TestCase):
    def setUp(self):
        T.clear_research_cache()

    def test_offline_skips_research(self):
        with patch.object(T.netcheck, "llm_reachable", return_value=False):
            r = T.research("某园区 100 路，存储 30 天")
        self.assertEqual(r["engine"], "offline")
        self.assertEqual(r["calls"], [])

    def test_no_key_skips_research(self):
        with patch.object(T.netcheck, "llm_reachable", return_value=True), \
             patch.object(T.C, "DEEPSEEK_API_KEY", ""):
            r = T.research("某园区 100 路")
        self.assertEqual(r["engine"], "rule")
        self.assertEqual(r["calls"], [])

    def test_llm_loop_executes_tools(self):
        """mock 两轮对话：第一轮提出 search_kb 调用，第二轮结束。"""
        call1 = _resp({"choices": [{"message": {
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "search_kb",
                "arguments": json.dumps({"keyword": "DH-AS3900C"}),
            }}],
        }}]})
        call2 = _resp({"choices": [{"message": {"content": "完成"}}]})

        def fake_post(url, **kwargs):
            self.assertEqual(kwargs["json"]["tool_choice"], "auto")
            # 回传给模型的 tool 消息里必须带真实工具结果
            msgs = kwargs["json"]["messages"]
            if any(m.get("role") == "tool" for m in msgs):
                tool_msg = next(m for m in msgs if m.get("role") == "tool")
                self.assertIn("found", tool_msg["content"])
                return call2
            return call1

        with patch.object(T.netcheck, "llm_reachable", return_value=True), \
             patch.object(T.C, "DEEPSEEK_API_KEY", "test-key"), \
             patch.object(T.requests, "post", side_effect=fake_post):
            r = T.research("预算对标：客户提到交换机 DH-AS3900C-24GT4GF-370")

        self.assertEqual(r["engine"], "llm")
        self.assertEqual(len(r["calls"]), 1)
        c = r["calls"][0]
        self.assertEqual(c["tool"], "search_kb")
        self.assertTrue(c["ok"])
        self.assertIn("found", c["result"])

    def test_research_result_cached(self):
        """同一段需求的 LLM 研究结果落缓存：第二次不再发任何请求。"""
        call1 = _resp({"choices": [{"message": {"content": "完成"}}]})
        with patch.object(T.netcheck, "llm_reachable", return_value=True), \
             patch.object(T.C, "DEEPSEEK_API_KEY", "test-key"), \
             patch.object(T.requests, "post", side_effect=[call1]) as fake_post:
            r1 = T.research("同一段需求")
            r2 = T.research("同一段需求")
        self.assertEqual(fake_post.call_count, 1)
        self.assertEqual(r1, r2)
        self.assertEqual(r1["engine"], "llm")

    def test_bad_tool_args_recorded_not_raised(self):
        call1 = _resp({"choices": [{"message": {
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "calc_storage", "arguments": "not-json",
            }}],
        }}]})
        call2 = _resp({"choices": [{"message": {"content": "完成"}}]})
        with patch.object(T.netcheck, "llm_reachable", return_value=True), \
             patch.object(T.C, "DEEPSEEK_API_KEY", "test-key"), \
             patch.object(T.requests, "post", side_effect=[call1, call2]):
            r = T.research("某园区 100 路存储 30 天")
        self.assertEqual(len(r["calls"]), 1)
        self.assertFalse(r["calls"][0]["ok"])
        self.assertEqual(r["calls"][0]["args"], {})


class GraphIntegrationTest(unittest.TestCase):
    """build_node 必须带出 research 记录，且研究失败不阻塞主流程。"""

    def test_build_node_carries_research(self):
        from agent import graph as G
        fake = {"engine": "llm", "calls": [{"tool": "search_kb",
                                            "args": {"keyword": "DH-IPC"},
                                            "ok": True, "result": "{}"}],
                "warnings": []}
        with patch.object(G, "run_research", return_value=fake), \
             patch.object(G, "parse_requirement",
                          return_value={"text": "某园区：周界20路，出入口10路，存储30天。",
                                        "engine": "rule", "scenes": [],
                                        "project_name": None, "warnings": []}):
            out = G.build_node({"text": "某园区：周界20路，出入口10路，存储30天。"})
        self.assertEqual(out["research"], fake)
        self.assertIsNotNone(out["spec"])

    def test_build_node_survives_research_crash(self):
        from agent import graph as G
        with patch.object(G, "run_research",
                          side_effect=RuntimeError("boom")), \
             patch.object(G, "parse_requirement",
                          return_value={"text": "某园区：周界20路，出入口10路，存储30天。",
                                        "engine": "rule", "scenes": [],
                                        "project_name": None, "warnings": []}):
            out = G.build_node({"text": "某园区：周界20路，出入口10路，存储30天。"})
        self.assertIsNotNone(out["spec"])
        self.assertEqual(out["research"]["calls"], [])


if __name__ == "__main__":
    unittest.main()
