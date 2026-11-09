"""LangGraph 冒烟测试：断点续跑 / human-in-loop / 校验失败条件回边。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.types import Command

from agent import graph as agent_graph
from app.builder import SpecGateError
from config.settings import DB_PATH

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


@unittest.skipUnless(DB_PATH.exists(), "知识库不存在，跳过 Agent 冒烟测试")
class AgentGraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "checkpoints.sqlite"
        self.graphs = []
        patcher = patch.object(agent_graph, "EXPORT_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 单测不联网：需求解析强制走规则引擎
        rule_patcher = patch.object(
            agent_graph, "parse_requirement",
            lambda text: {"text": text, "engine": "rule", "scenes": [],
                          "project_name": None, "warnings": []},
        )
        rule_patcher.start()
        self.addCleanup(rule_patcher.stop)

    def tearDown(self):
        for graph in self.graphs:
            graph.checkpointer.conn.close()
        self.tmp.cleanup()

    def _graph(self):
        graph = agent_graph.build_graph(self.db)
        self.graphs.append(graph)
        return graph

    def test_review_interrupt_then_approve_exports(self):
        graph = self._graph()
        cfg = {"configurable": {"thread_id": "t-approve"}}

        out = graph.invoke({"text": TEXT}, cfg)
        self.assertTrue(out.get("__interrupt__"), "应停在 Spec 审阅处")
        self.assertEqual(out["__interrupt__"][0].value["type"], "review")
        self.assertIsNotNone(out.get("spec"))

        res = graph.invoke(Command(resume="approve"), cfg)
        self.assertEqual(res["decision"], "approve")
        self.assertTrue(Path(res["outputs"]["png"]).is_file())
        self.assertTrue(Path(res["outputs"]["docx"]).is_file())

    def test_gate_failure_back_edge_then_supplement(self):
        graph = self._graph()
        cfg = {"configurable": {"thread_id": "t-gap"}}
        real_build = agent_graph.build_spec
        calls = {"n": 0}

        def flaky_build(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SpecGateError(["以下场景点位数未确定：主干道"])
            return real_build(*args, **kwargs)

        with patch.object(agent_graph, "build_spec", flaky_build):
            out = graph.invoke({"text": TEXT}, cfg)
            self.assertEqual(out["__interrupt__"][0].value["type"], "gap")
            self.assertIsNone(out.get("spec"))

            resumed = graph.invoke(Command(resume="补充：主干道 15 路"), cfg)
            self.assertEqual(resumed["rounds"], 1)
            self.assertIsNotNone(resumed.get("spec"))
            self.assertEqual(resumed["__interrupt__"][0].value["type"], "review")

            done = graph.invoke(Command(resume="approve"), cfg)
            self.assertEqual(done["decision"], "approve")

    def test_revise_feedback_loops_back_to_build(self):
        graph = self._graph()
        cfg = {"configurable": {"thread_id": "t-revise"}}

        graph.invoke({"text": TEXT}, cfg)
        res = graph.invoke(Command(resume="补充：停车场 10 路"), cfg)
        self.assertIn("修改：补充：停车场 10 路", res["text"])
        self.assertIn("停车场", [r["target"] for r in res["spec"]["requirements"]])
        self.assertEqual(res["__interrupt__"][0].value["type"], "review")

        graph.invoke(Command(resume="approve"), cfg)

    def test_resume_from_new_graph_instance(self):
        cfg = {"configurable": {"thread_id": "t-resume"}}
        self._graph().invoke({"text": TEXT}, cfg)

        graph2 = self._graph()
        state = graph2.get_state(cfg)
        self.assertIsNotNone(state.values.get("spec"), "新实例应能读到落盘 checkpoint")
        res = graph2.invoke(Command(resume="approve"), cfg)
        self.assertEqual(res["decision"], "approve")


if __name__ == "__main__":
    unittest.main()
