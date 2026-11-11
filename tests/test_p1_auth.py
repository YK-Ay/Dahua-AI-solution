"""P1 回归：账号体系 / 注册空白开始 / 跨账号数据隔离 / 选型理由。"""
import itertools
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.api as A
from app import auth, metrics, sessions as SS

TEXT = "某智慧园区综合安防项目：周界80路，出入口20路，存储30天。"

SPEC = {
    "project": {"name": "某智慧园区", "industry": "智慧园区"},
    "requirements": [{"target": "周界", "lanes": 80}, {"target": "出入口", "lanes": 20}],
    "devices": [{"category": "网络摄像机", "model": "DH-IPC", "qty": 100,
                 "unit_price": 500}],
    "storage": {"retention_days": 30},
}


def fake_data(tid="t-p1", status="done"):
    name = f"{tid}.docx"
    return {
        "thread_id": tid,
        "status": status,
        "text": TEXT,
        "spec": SPEC,
        "files": {"docx": {"name": name, "url": f"/api/export/{name}"}},
    }


class AuthStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patch = patch.object(auth, "DB_PATH", self.dir / "auth.sqlite")
        self.patch.start()
        auth.reset_for_tests()

    def tearDown(self):
        self.patch.stop()
        auth.reset_for_tests()
        self.tmp.cleanup()

    def test_register_and_authenticate(self):
        user = auth.register("售前_张三", "secret123")
        self.assertEqual(user["username"], "售前_张三")
        self.assertNotIn("password_hash", user)
        self.assertEqual(auth.authenticate("售前_张三", "secret123")["id"], user["id"])
        with self.assertRaises(ValueError):
            auth.authenticate("售前_张三", "wrong-pass")

    def test_duplicate_and_invalid_credentials(self):
        auth.register("alice", "secret123")
        with self.assertRaises(ValueError):
            auth.register("alice", "another123")
        with self.assertRaises(ValueError):
            auth.register("ab", "secret123")           # 用户名太短
        with self.assertRaises(ValueError):
            auth.register("bob", "123")                # 密码太短
        with self.assertRaises(ValueError):
            auth.register("bad name", "secret123")     # 含空格

    def test_token_lifecycle(self):
        user = auth.register("carol", "secret123")
        token = auth.create_token(user["id"])
        self.assertEqual(auth.user_by_token(token)["username"], "carol")
        self.assertIsNone(auth.user_by_token("not-a-token"))

        auth.delete_token(token)
        self.assertIsNone(auth.user_by_token(token))

        # 过期 token 不再可用，且能被 purge 清掉
        with auth._lock, auth._conn() as conn:
            conn.execute(
                "INSERT INTO tokens(token, user_id, created_at, expires_at) "
                "VALUES(?,?,?,?)",
                ("expired-token", user["id"], "2000-01-01T00:00:00", "2000-01-02T00:00:00"),
            )
        self.assertIsNone(auth.user_by_token("expired-token"))
        auth.purge_expired()
        with auth._lock, auth._conn() as conn:
            left = conn.execute(
                "SELECT COUNT(*) AS c FROM tokens WHERE token=?", ("expired-token",)
            ).fetchone()["c"]
        self.assertEqual(left, 0)


class TokenExpiryTest(unittest.TestCase):
    """token 过期判定只看 expires_at，不依赖服务端会话内存。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patch = patch.object(auth, "DB_PATH", self.dir / "auth.sqlite")
        self.patch.start()
        auth.reset_for_tests()
        self.user = auth.register("dave", "secret123")

    def tearDown(self):
        self.patch.stop()
        auth.reset_for_tests()
        self.tmp.cleanup()

    def test_expiry_boundary(self):
        token = auth.create_token(self.user["id"])
        self.assertIsNotNone(auth.user_by_token(token))
        with auth._lock, auth._conn() as conn:
            conn.execute("UPDATE tokens SET expires_at=? WHERE token=?",
                         ("2001-01-01T00:00:00", token))
        self.assertIsNone(auth.user_by_token(token))


class AuthApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patches = [
            patch.object(auth, "DB_PATH", self.dir / "auth.sqlite"),
            patch.object(SS, "DB_PATH", self.dir / "sessions.sqlite"),
            patch.object(metrics, "DB_PATH", self.dir / "metrics.sqlite"),
            patch.object(A, "EXPORT_DIR", self.dir),
        ]
        for p in self.patches:
            p.start()
        auth.reset_for_tests()
        SS.reset_for_tests()
        metrics.reset_for_tests()
        self.client = TestClient(A.app)
        self.client.get("/api/health")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        auth.reset_for_tests()
        SS.reset_for_tests()
        metrics.reset_for_tests()
        self.tmp.cleanup()

    def test_register_me_logout(self):
        r = self.client.post("/api/auth/register",
                             json={"username": "erin", "password": "secret123"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["username"], "erin")
        self.assertTrue(self.client.cookies.get(A.AUTH_COOKIE))

        me = self.client.get("/api/auth/me").json()
        self.assertEqual(me["user"]["username"], "erin")

        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"])

    def test_login_errors(self):
        r = self.client.post("/api/auth/login",
                             json={"username": "nobody", "password": "secret123"})
        self.assertEqual(r.status_code, 401)

        self.client.post("/api/auth/register",
                         json={"username": "frank", "password": "secret123"})
        self.client.post("/api/auth/logout")
        r = self.client.post("/api/auth/login",
                             json={"username": "frank", "password": "bad-pass"})
        self.assertEqual(r.status_code, 401)

        r = self.client.post("/api/auth/register",
                             json={"username": "frank", "password": "secret123"})
        self.assertEqual(r.status_code, 400)


class AnonClaimIsolationTest(unittest.TestCase):
    """注册不继承匿名会话（空白开始），登录后跨浏览器仍可见，且账号间互相隔离。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.patches = [
            patch.object(auth, "DB_PATH", self.dir / "auth.sqlite"),
            patch.object(SS, "DB_PATH", self.dir / "sessions.sqlite"),
            patch.object(metrics, "DB_PATH", self.dir / "metrics.sqlite"),
            patch.object(A, "EXPORT_DIR", self.dir),
            patch.object(A.agent_service, "start",
                         lambda text, **kw: dict(fake_data(tid=f"t-{next(self.seq)}"))),
            patch.object(A.agent_service, "state_of",
                         lambda tid: dict(fake_data(tid=tid))),
        ]
        for p in self.patches:
            p.start()
        auth.reset_for_tests()
        SS.reset_for_tests()
        metrics.reset_for_tests()
        self.seq = itertools.count(1)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        auth.reset_for_tests()
        SS.reset_for_tests()
        metrics.reset_for_tests()
        self.tmp.cleanup()

    def _boot(self):
        client = TestClient(A.app)
        client.get("/api/health")          # 触发匿名 cookie
        return client

    def _start(self, client):
        d = client.post("/api/agent/start", json={"text": TEXT}).json()
        (self.dir / f"{d['thread_id']}.docx").write_bytes(b"PK")
        return d["thread_id"]

    def test_register_starts_clean_then_isolate(self):
        anon = self._boot()
        t1 = self._start(anon)
        self.assertEqual(anon.get(f"/api/export/{t1}.docx").status_code, 200)

        # 注册：不认领匿名会话，并换发新的匿名标识 → 新账号空白开始
        r = anon.post("/api/auth/register",
                      json={"username": "owner_a", "password": "secret123"}).json()
        self.assertEqual(r["claimed"], {"sessions": 0, "exports": 0})
        self.assertEqual(anon.get("/api/auth/me").json()["user"]["username"], "owner_a")
        self.assertEqual(anon.get("/api/agent/sessions").json()["items"], [])
        self.assertEqual(anon.get(f"/api/agent/{t1}/state").status_code, 404)
        self.assertEqual(anon.get(f"/api/export/{t1}.docx").status_code, 403)

        # 登录态下新建的会话挂在账号上
        t2 = self._start(anon)
        self.assertEqual(anon.get(f"/api/agent/{t2}/state").status_code, 200)
        self.assertEqual(len(anon.get("/api/agent/sessions").json()["items"]), 1)

        # 另一个账号：既看不到会话，也下载不了文件
        other = self._boot()
        other.post("/api/auth/register",
                   json={"username": "owner_b", "password": "secret123"})
        self.assertEqual(other.get("/api/agent/sessions").json()["items"], [])
        self.assertEqual(other.get(f"/api/agent/{t2}/state").status_code, 404)
        self.assertEqual(other.get(f"/api/export/{t2}.docx").status_code, 403)
        self.assertEqual(other.delete(f"/api/agent/sessions/{t2}").status_code, 404)

        # 同账号换浏览器登录：靠 user_id 仍可续用
        another = self._boot()
        login = another.post("/api/auth/login",
                             json={"username": "owner_a", "password": "secret123"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(another.get(f"/api/agent/{t2}/state").status_code, 200)
        self.assertEqual(another.get(f"/api/export/{t2}.docx").status_code, 200)
        self.assertEqual(len(another.get("/api/agent/sessions").json()["items"]), 1)


class SelectionReasonTest(unittest.TestCase):
    """选型理由：方案库配单优先 + 理由写进 Spec 与 Word。"""

    def test_pick_from_boms_returns_refs(self):
        from app.kb import ProductRepo
        with ProductRepo() as repo:
            row = repo.conn.execute(
                "SELECT b.industry AS industry, p.class_name AS cls FROM bom_items bi "
                "JOIN boms b ON b.scheme_id = bi.scheme_id "
                "JOIN products p ON p.model = bi.model "
                "WHERE p.price_stand IS NOT NULL "
                "GROUP BY b.industry, p.class_name ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()
            if row is None:
                self.skipTest("方案配单为空，跳过")
            hit = repo.pick_from_boms(row["industry"], [row["cls"]], [])
        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit["refs"], 1)
        self.assertTrue(hit["model"])

    def test_spec_reasons_and_word_column(self):
        import os
        import tempfile
        from app.builder import build_spec
        from app.word import render_docx

        old = os.environ.get("APP_OFFLINE")
        os.environ["APP_OFFLINE"] = "1"
        try:
            spec = build_spec(TEXT)
        finally:
            if old is None:
                os.environ.pop("APP_OFFLINE", None)
            else:
                os.environ["APP_OFFLINE"] = old
        self.assertTrue(spec.devices)
        self.assertTrue(all(d.reason for d in spec.devices),
                        "每类设备都应带选型依据")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out = render_docx(spec, Path(tmp) / "s.docx")
            self.assertTrue(out.is_file())

            from docx import Document
            doc = Document(str(out))
            table = None
            for t in doc.tables:
                header = [c.text for c in t.rows[0].cells]
                if "选型依据" in header:
                    table = t
                    break
            self.assertIsNotNone(table, "Word 设备表缺少「选型依据」列")
            col = [c.text for c in table.rows[0].cells].index("选型依据")
            bodies = [r.cells[col].text for r in table.rows[1:]]
            self.assertTrue(any(body and body != "—" for body in bodies),
                            "Word 设备表应写出选型依据")


if __name__ == "__main__":
    unittest.main()
