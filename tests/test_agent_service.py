"""Agent Web 层回归：离线降级、断点续跑、审阅改参回边重算。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import graph as G  # noqa: E402
from agent import service as S  # noqa: E402
from agent.parser import _digits_traceable, parse_requirement  # noqa: E402
from app.builder import SpecGateError  # noqa: E402

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


class ParserTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("APP_OFFLINE")
        os.environ["APP_OFFLINE"] = "1"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APP_OFFLINE", None)
        else:
            os.environ["APP_OFFLINE"] = self._old

    def test_offline_uses_rule_engine(self):
        r = parse_requirement(TEXT)
        self.assertEqual(r["engine"], "offline")
        self.assertEqual(r["text"], TEXT)
        self.assertTrue(r["warnings"])

    def test_digits_must_trace_back_to_original(self):
        self.assertTrue(_digits_traceable("存储30天", TEXT))
        self.assertFalse(_digits_traceable("存储99天", TEXT))
        self.assertFalse(_digits_traceable("120路", TEXT))


class AgentServiceTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("APP_OFFLINE")
        os.environ["APP_OFFLINE"] = "1"          # 单测绝不联网
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.graph = G.build_graph(self.dir / "checkpoints.sqlite")
        self.p1 = patch.object(G, "EXPORT_DIR", self.dir)
        self.p2 = patch.object(S, "get_graph", lambda: self.graph)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p2.stop()
        self.p1.stop()
        self.graph.checkpointer.conn.close()
        self.tmp.cleanup()
        if self._old is None:
            os.environ.pop("APP_OFFLINE", None)
        else:
            os.environ["APP_OFFLINE"] = self._old

    def test_start_state_resume_full_flow(self):
        d = S.start(TEXT)
        self.assertEqual(d["status"], "awaiting_input")
        self.assertEqual(d["interrupt_type"], "review")
        self.assertIsNotNone(d["spec"])
        self.assertEqual(d["parser"]["engine"], "offline")

        tid = d["thread_id"]
        snap = S.state_of(tid)
        self.assertEqual(snap["status"], "awaiting_input")
        self.assertIsNotNone(snap["spec"])

        done = S.resume(tid, {"decision": "approve"})
        self.assertEqual(done["status"], "done")
        self.assertIn("png", done["files"])
        self.assertIn("docx", done["files"])
        self.assertTrue(done["files"]["docx"]["url"].startswith("/api/export/"))

    def test_review_patch_triggers_recalculation(self):
        d = S.start(TEXT)
        tid = d["thread_id"]
        revised = S.resume(tid, {
            "decision": "revise",
            "patch": {"lanes": 120, "retention_days": 45},
            "feedback": "按审阅意见调整",
        })
        self.assertEqual(revised["interrupt_type"], "review")
        spec = revised["spec"]
        total = sum(r["lanes"] for r in spec["requirements"])
        self.assertEqual(total, 120)
        self.assertEqual(spec["storage"]["retention_days"], 45)

    def test_patch_lower_than_explicit_scene_sum(self):
        # 原文各场景显式加起来 170 路，审阅改成 100 路时也必须真的变成 100
        d = S.start("某园区：周界80路，出入口20路，主干道30路，室内大厅40路，存储30天。")
        revised = S.resume(d["thread_id"], {
            "decision": "revise", "patch": {"lanes": 100}, "feedback": "缩减规模",
        })
        total = sum(r["lanes"] for r in revised["spec"]["requirements"])
        self.assertEqual(total, 100)

    def test_gap_interrupt_then_supplement(self):
        real_build = G.build_spec
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SpecGateError(["主干道点位数未确定"])
            return real_build(*args, **kwargs)

        with patch.object(G, "build_spec", flaky):
            d = S.start(TEXT)
            self.assertEqual(d["interrupt_type"], "gap")
            resumed = S.resume(d["thread_id"], {"text": "补充：主干道 15 路"})
            self.assertEqual(resumed["rounds"], 1)
            self.assertEqual(resumed["interrupt_type"], "review")

    def test_unknown_thread_raises(self):
        with self.assertRaises(KeyError):
            S.state_of("not-exist-thread")


if __name__ == "__main__":
    unittest.main()
