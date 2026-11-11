"""P0 回归：会话索引 / 方案版本 / 导出鉴权。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.api as A
from app import sessions as SS

SPEC = {
    "project": {"name": "某智慧园区", "industry": "智慧园区"},
    "requirements": [{"target": "周界", "lanes": 80}, {"target": "出入口", "lanes": 20}],
    "devices": [{"category": "网络摄像机", "model": "DH-IPC", "qty": 100,
                 "unit_price": 500}],
    "storage": {"retention_days": 30},
}


def data_of(tid="t-1", status="awaiting_input", files=None):
    return {
        "thread_id": tid,
        "status": status,
        "text": "某智慧园区：周界80路、出入口20路，存储30天",
        "spec": SPEC,
        "files": files or {},
    }


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patches = [patch.object(SS, "DB_PATH", self.dir / "sessions.sqlite")]
        for p in self.patches:
            p.start()
        SS.reset_for_tests()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        SS.reset_for_tests()
        self.tmp.cleanup()

    def test_record_list_rename_delete(self):
        SS.record("cid-a", data_of())
        items = SS.list_sessions("cid-a")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "某智慧园区")
        self.assertEqual(items[0]["lanes"], 100)
        self.assertEqual(items[0]["retention_days"], 30)
        self.assertEqual(SS.list_sessions("cid-b")["items"], [])

        self.assertTrue(SS.rename_session("cid-a", "t-1", "园区一期"))
        self.assertFalse(SS.rename_session("cid-b", "t-1", "越权改名"))
        self.assertEqual(SS.list_sessions("cid-a")["items"][0]["title"], "园区一期")

        # 别人的 thread 不能被覆盖，也不能读取
        self.assertIsNone(SS.record("cid-b", data_of()))
        self.assertIsNone(SS.get_session("cid-b", "t-1"))

        removed = SS.delete_session("cid-a", "t-1")
        self.assertEqual(removed["thread_id"], "t-1")
        self.assertEqual(SS.list_sessions("cid-a")["items"], [])

    def test_versions_dedupe_and_increment(self):
        f1 = {"docx": {"name": "v1.docx", "url": "/api/export/v1.docx"}}
        SS.record("cid-a", data_of(status="done", files=f1))
        SS.record("cid-a", data_of(status="done", files=f1))   # 重放同一产物
        vs = SS.list_versions("cid-a", "t-1")
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0]["version_no"], 1)
        self.assertEqual(vs[0]["summary"]["price"], 50000)

        f2 = {"docx": {"name": "v2.docx", "url": "/api/export/v2.docx"}}
        SS.record("cid-a", data_of(status="done", files=f2))
        vs = SS.list_versions("cid-a", "t-1")
        self.assertEqual([v["version_no"] for v in vs], [2, 1])
        self.assertIsNone(SS.list_versions("cid-b", "t-1"))

    def test_derived_export_does_not_add_version(self):
        f1 = {"docx": {"name": "v1.docx", "url": "/api/export/v1.docx"}}
        SS.record("cid-a", data_of(status="done", files=f1))
        f2 = dict(f1)
        f2["pdf"] = {"name": "v1.pdf", "url": "/api/export/v1.pdf"}
        self.assertIsNone(
            SS.record("cid-a", data_of(status="done", files=f2), version=False)
        )
        self.assertEqual(len(SS.list_versions("cid-a", "t-1")), 1)

    def test_exports_ownership(self):
        SS.register_exports("cid-a", None, ["a.docx", "b.png"])
        self.assertTrue(SS.owns_file("cid-a", "a.docx"))
        self.assertFalse(SS.owns_file("cid-b", "a.docx"))
        self.assertFalse(SS.owns_file("", "a.docx"))
        self.assertFalse(SS.owns_file("cid-a", "nope.docx"))


class ExportAuthApiTest(unittest.TestCase):
    """导出接口：归属校验 + 防路径穿越（用真实 HTTP 栈验证）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patches = [
            patch.object(SS, "DB_PATH", self.dir / "sessions.sqlite"),
            patch.object(A, "EXPORT_DIR", self.dir),
        ]
        for p in self.patches:
            p.start()
        SS.reset_for_tests()
        (self.dir / "own.docx").write_bytes(b"PK")
        self.client = TestClient(A.app)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        SS.reset_for_tests()
        self.tmp.cleanup()

    def _cid(self, client):
        client.get("/api/health")          # 触发匿名 cookie 下发
        return client.cookies.get(A.CLIENT_COOKIE)

    def test_unowned_file_forbidden_and_owned_allowed(self):
        cid = self._cid(self.client)
        self.assertTrue(cid)
        r = self.client.get("/api/export/own.docx")
        self.assertEqual(r.status_code, 403)
        SS.register_exports(cid, None, ["own.docx"])
        r = self.client.get("/api/export/own.docx")
        self.assertEqual(r.status_code, 200)

        other = TestClient(A.app)
        self._cid(other)
        self.assertEqual(other.get("/api/export/own.docx").status_code, 403)

    def test_path_traversal_blocked(self):
        self._cid(self.client)
        r = self.client.get("/api/export/..%5Csecret.docx")
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/api/export/missing.docx")
        self.assertEqual(r.status_code, 404)


class SessionApiTest(unittest.TestCase):
    """会话接口端到端：启动 → 历史列表 → 跨客户端隔离 → 删除。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patches = [
            patch.object(SS, "DB_PATH", self.dir / "sessions.sqlite"),
            patch.object(A, "EXPORT_DIR", self.dir),
            patch.object(A.agent_service, "delete_thread", lambda tid: None),
        ]
        for p in self.patches:
            p.start()
        SS.reset_for_tests()
        (self.dir / "x.docx").write_bytes(b"PK")
        self.fake = data_of(tid="t-api", status="done",
                            files={"docx": {"name": "x.docx",
                                            "url": "/api/export/x.docx"}})
        self.p3 = patch.object(A.agent_service, "start", lambda *a, **k: dict(self.fake))
        self.p4 = patch.object(A.agent_service, "state_of", lambda tid: dict(self.fake))
        self.p3.start()
        self.p4.start()
        self.client = TestClient(A.app)

    def tearDown(self):
        self.p3.stop()
        self.p4.stop()
        for p in self.patches:
            p.stop()
        SS.reset_for_tests()
        self.tmp.cleanup()

    def test_history_isolation_and_delete(self):
        d = self.client.post("/api/agent/start", json={"text": "某园区"}).json()
        self.assertEqual(d["thread_id"], "t-api")

        items = self.client.get("/api/agent/sessions").json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["version_count"], 1)

        versions = self.client.get("/api/agent/sessions/t-api/versions").json()["items"]
        self.assertEqual(versions[0]["version_no"], 1)

        self.assertEqual(self.client.patch("/api/agent/sessions/t-api",
                                           json={"title": "改名后"}).status_code, 200)

        other = TestClient(A.app)
        self.assertEqual(other.get("/api/agent/sessions").json()["items"], [])
        self.assertEqual(other.get("/api/agent/t-api/state").status_code, 404)
        self.assertEqual(other.delete("/api/agent/sessions/t-api").status_code, 404)

        r = self.client.delete("/api/agent/sessions/t-api")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["removed_files"], 1)
        self.assertFalse((self.dir / "x.docx").exists())


if __name__ == "__main__":
    unittest.main()
