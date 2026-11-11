"""P2 回归：报价单 / 在线编辑重算 / PPT 导出 / 埋点。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.api as A
from agent import graph as G
from agent import service as S
from app import metrics, sessions as SS
from app.edit import apply_edit
from app.models import Spec
from app.quote import build_quote

TEXT = "某智慧园区综合安防项目：周界20路，出入口10路，室内大厅5路，存储30天。"


def _real_models():
    from app.kb import ProductRepo
    with ProductRepo() as repo:
        cams = [r["model"] for r in repo.search(class_name="网络摄像机", limit=2)]
        nvr = [r["model"] for r in repo.search(class_name="通用存储录像机", limit=1)]
    if len(cams) < 2 or not nvr:
        return None
    return cams[0], cams[1], nvr[0]


def make_spec(models=None) -> Spec:
    cam1, cam2, nvr = models or ("DH-IPC-A", "DH-IPC-B", "DH-NVR")
    return Spec.model_validate({
        "project": {"name": "某园区", "industry": "智慧园区"},
        "requirements": [{"target": "周界", "intent": "周界防范", "lanes": 20},
                         {"target": "出入口", "intent": "人员通行", "lanes": 10}],
        "devices": [
            {"category": "网络摄像机", "model": cam1, "qty": 20, "unit_price": 500},
            {"category": "网络摄像机", "model": cam2, "qty": 10, "unit_price": 800},
            {"category": "NVR", "model": nvr, "qty": 1, "unit_price": None},
        ],
        "storage": {"retention_days": 30, "raw_tb": 10, "with_redundancy_tb": 11},
        "network": {"uplink_mbps": 30},
    })


class QuoteTest(unittest.TestCase):
    def test_total_and_unpriced(self):
        q = build_quote(make_spec())
        self.assertEqual(q["total"], 20 * 500 + 10 * 800)
        self.assertEqual(q["priced"], 2)
        self.assertEqual(q["unpriced"], 1)
        self.assertEqual(len(q["lines"]), 3)
        self.assertEqual(q["lines"][0]["amount"], 10000)


class EditSpecTest(unittest.TestCase):
    def setUp(self):
        self.models = _real_models()
        if not self.models:
            self.skipTest("产品库缺少可用摄像机 / NVR")

    def test_qty_and_model_edit(self):
        from app.kb import ProductRepo
        cam1, cam2, nvr = self.models
        with ProductRepo() as repo:
            price = repo.info(cam1)["price_stand"]
        edited = apply_edit(make_spec(self.models), TEXT, {
            "devices": [{"index": 1, "qty": 30},
                        {"index": 2, "model": cam1, "qty": 4}],
        })
        self.assertEqual(edited.devices[0].qty, 30)
        # 换型号后名称与单价必须取自产品库，不能沿用旧值
        self.assertEqual(edited.devices[1].model, cam1)
        self.assertEqual(edited.devices[1].unit_price, price)
        self.assertEqual(edited.devices[1].qty, 4)

    def test_unknown_model_rejected(self):
        with self.assertRaises(ValueError):
            apply_edit(make_spec(self.models), TEXT,
                       {"devices": [{"index": 1, "model": "NOT-EXIST-MODEL"}]})

    def test_invalid_lanes_rejected(self):
        with self.assertRaises(ValueError):
            apply_edit(make_spec(self.models), TEXT, {"lanes": 0})


class EditFlowTest(unittest.TestCase):
    """真实图 + 临时目录：生成交付物后在线改数量，产物与版本都要更新。"""

    def setUp(self):
        self._old = os.environ.get("APP_OFFLINE")
        os.environ["APP_OFFLINE"] = "1"
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

    def test_edit_after_done_regenerates_files(self):
        d = S.start(TEXT)
        done = S.resume(d["thread_id"], {"decision": "approve"})
        self.assertEqual(done["status"], "done")
        self.assertIn("pptx", done["files"])
        self.assertTrue((self.dir / done["files"]["pptx"]["name"]).is_file())

        spec_before = done["spec"]
        target = spec_before["devices"][0]
        edited = S.edit_outputs(d["thread_id"], {
            "devices": [{"index": 1, "qty": target["qty"] + 3}],
        })
        self.assertEqual(edited["status"], "done")
        self.assertEqual(edited["spec"]["devices"][0]["qty"], target["qty"] + 3)
        # 重新渲染 → 新文件名（时间戳），旧产物仍可回溯
        self.assertNotEqual(edited["files"]["docx"]["name"], done["files"]["docx"]["name"])
        self.assertTrue((self.dir / edited["files"]["docx"]["name"]).is_file())

    def test_edit_lanes_rebuilds_storage(self):
        d = S.start(TEXT)
        S.resume(d["thread_id"], {"decision": "approve"})
        edited = S.edit_outputs(d["thread_id"], {"lanes": 60, "retention_days": 60})
        total = sum(r["lanes"] for r in edited["spec"]["requirements"])
        self.assertEqual(total, 60)
        self.assertEqual(edited["spec"]["storage"]["retention_days"], 60)


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.p = patch.object(metrics, "DB_PATH", self.dir / "metrics.sqlite")
        self.p.start()
        metrics.reset_for_tests()

    def tearDown(self):
        self.p.stop()
        metrics.reset_for_tests()
        self.tmp.cleanup()

    def test_overview_counts_distinct_threads(self):
        metrics.record(metrics.START, "c1", "t1")
        metrics.record(metrics.START, "c1", "t1")
        metrics.record(metrics.START, "c1", "t2")
        metrics.record(metrics.APPROVE, "c1", "t1")
        metrics.record("export", "c1", "t1", filename="a.docx")
        o = metrics.overview("c1")
        self.assertEqual(o["total_start"], 2)
        self.assertEqual(o["total_done"], 1)
        self.assertEqual(o["today_start"], 2)
        self.assertEqual(o["today_export"], 1)
        self.assertEqual(o["done_rate"], 0.5)

    def test_metrics_failure_never_raises(self):
        with patch.object(metrics, "_conn", side_effect=__import__("sqlite3").Error):
            metrics.record("start", "c1", "t1")   # 不抛异常即通过


class P2ApiTest(unittest.TestCase):
    """接口层：报价、编辑鉴权、PDF 不可用时的明确报错。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patches = [
            patch.object(SS, "DB_PATH", self.dir / "sessions.sqlite"),
            patch.object(metrics, "DB_PATH", self.dir / "metrics.sqlite"),
            patch.object(A, "EXPORT_DIR", self.dir),
        ]
        for p in self.patches:
            p.start()
        SS.reset_for_tests()
        metrics.reset_for_tests()
        self.spec = make_spec().model_dump()
        self.fake = {"thread_id": "t-api", "status": "done", "spec": self.spec,
                     "text": TEXT, "files": {}}
        self.p3 = patch.object(A.agent_service, "state_of", lambda tid: dict(self.fake))
        self.p4 = patch.object(A.agent_service, "edit_outputs",
                               lambda tid, patch: {**self.fake, "status": "done"})
        self.p3.start()
        self.p4.start()
        self.client = TestClient(A.app)
        self.client.get("/api/health")

    def tearDown(self):
        self.p3.stop()
        self.p4.stop()
        for p in self.patches:
            p.stop()
        SS.reset_for_tests()
        metrics.reset_for_tests()
        self.tmp.cleanup()

    def test_quote_endpoint(self):
        q = self.client.get("/api/agent/t-api/quote").json()
        self.assertEqual(q["total"], 20 * 500 + 10 * 800)

    def test_edit_validation_errors(self):
        r = self.client.post("/api/agent/t-api/edit", json={})
        self.assertEqual(r.status_code, 400)

    def test_pdf_reports_unavailable(self):
        def boom(tid):
            raise RuntimeError("缺少 pywin32，无法调用 Word 转 PDF")
        with patch.object(A.agent_service, "export_pdf", boom):
            r = self.client.post("/api/agent/t-api/pdf")
        self.assertEqual(r.status_code, 503)
        self.assertIn("PDF", r.json()["detail"]["message"])


if __name__ == "__main__":
    unittest.main()
