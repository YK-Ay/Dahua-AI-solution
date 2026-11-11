"""多 Agent 编排：主 Agent 按 stage 调度五个子 Agent，每步产物可单独审计。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.types import Command

from agent import graph as G
from config.settings import DB_PATH

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


class RouteTest(unittest.TestCase):
    def test_gate_routes_to_gap_then_abort(self):
        self.assertEqual(G.route_from_supervisor({"stage": "gate", "rounds": 0}), "ask_gap")
        self.assertEqual(G.route_from_supervisor({"stage": "gate", "rounds": 3}), "abort")

    def test_unknown_stage_falls_back_to_intent(self):
        self.assertEqual(G.route_from_supervisor({"stage": "???"}), "intent")
        self.assertEqual(G.route_from_supervisor({}), "intent")


@unittest.skipUnless(DB_PATH.exists(), "知识库不存在，跳过 Agent 编排测试")
class MultiAgentFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.graphs = []
        patcher = patch.object(G, "EXPORT_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        rule_patcher = patch.object(
            G, "parse_requirement",
            lambda text: {"text": text, "engine": "rule", "scenes": [],
                          "project_name": None, "warnings": []},
        )
        rule_patcher.start()
        self.addCleanup(rule_patcher.stop)
        research_patcher = patch.object(
            G, "run_research", return_value={"engine": "offline", "calls": [], "warnings": []}
        )
        research_patcher.start()
        self.addCleanup(research_patcher.stop)

    def tearDown(self):
        for graph in self.graphs:
            graph.checkpointer.conn.close()
        self.tmp.cleanup()

    def _graph(self):
        graph = G.build_graph(self.dir / "ckpt.sqlite")
        self.graphs.append(graph)
        return graph

    def test_each_subagent_leaves_auditable_output(self):
        cfg = {"configurable": {"thread_id": "t-stages"}}
        out = self._graph().invoke({"text": TEXT}, cfg)
        self.assertEqual(out["__interrupt__"][0].value["type"], "review")
        self.assertEqual(out["stage"], "review")

        intent = out["intent"]
        self.assertEqual(intent["industry"], "智慧园区")
        self.assertEqual(sum(r["lanes"] for r in intent["requirements"]), 35)

        selection = out["selection"]
        self.assertEqual(len(selection["scenes"]), len(intent["requirements"]))
        self.assertTrue(all(s["model"] for s in selection["scenes"]))

        bom = out["bom"]
        camera_lanes = sum(s["lanes"] for s in selection["scenes"])
        self.assertEqual(bom["lanes_total"], camera_lanes)
        self.assertTrue(any(d["category"] == "NVR" for d in bom["devices"]))
        self.assertTrue(any("平台" in d["category"] for d in bom["devices"]))

        self.assertEqual(out["spec"]["project"]["industry"], "智慧园区")
        self.assertAlmostEqual(out["spec"]["network"]["uplink_mbps"],
                               bom["bandwidth"], places=2)

    def test_revise_rebuilds_every_stage(self):
        cfg = {"configurable": {"thread_id": "t-revise"}}
        graph = self._graph()
        first = graph.invoke({"text": TEXT}, cfg)
        old_model = first["selection"]["scenes"][0]["model"]

        revised = graph.invoke(Command(resume={
            "decision": "revise",
            "patch": {"lanes": 120, "retention_days": 45},
            "feedback": "按审阅意见调整",
        }), cfg)
        self.assertEqual(revised["intent"]["total"], 120)
        self.assertEqual(revised["spec"]["storage"]["retention_days"], 45)
        self.assertEqual(sum(r["lanes"] for r in revised["spec"]["requirements"]), 120)
        # 重建后的选型仍必须落在产品库里
        self.assertTrue(revised["selection"]["scenes"])
        self.assertEqual(len(revised["bom"]["devices"]),
                         len(revised["spec"]["devices"]))
        self.assertNotEqual(revised["stage"], "gate")
        self.assertTrue(old_model)


if __name__ == "__main__":
    unittest.main()
