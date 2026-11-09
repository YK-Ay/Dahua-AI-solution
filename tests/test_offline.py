"""离线降级单测：声明 APP_OFFLINE=1 后不得发起任何网络连接。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import netcheck  # noqa: E402
from app.rag import config as C, service  # noqa: E402


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("APP_OFFLINE")
        os.environ["APP_OFFLINE"] = "1"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APP_OFFLINE", None)
        else:
            os.environ["APP_OFFLINE"] = self._old

    def test_forced_offline_flag(self):
        self.assertTrue(netcheck.forced_offline())

    def test_no_socket_is_opened(self):
        with patch("socket.create_connection", side_effect=AssertionError("离线时不应联网")):
            self.assertFalse(netcheck.llm_reachable(C.DEEPSEEK_BASE_URL, force=True))
            self.assertFalse(netcheck.mall_reachable(force=True))

    def test_status_marks_offline(self):
        st = service.status()
        self.assertTrue(st["ok"])
        self.assertTrue(st["offline"])
        self.assertFalse(st["network_online"])
        self.assertFalse(st["llm_enabled"])
        self.assertIn("离线", st["mode"])

    def test_ask_degrades_to_local_extractive(self):
        r = service.ask("小区监控需要哪些摄像机", mode="solution", top_k=3)
        self.assertTrue(r["ok"])
        self.assertIn(r["mode"], ("extractive", "no_evidence"))
        self.assertEqual(r["llm_error"], "")
        self.assertTrue(r["answer"])
        # 降级答案必须明确告诉用户当前是本地检索摘要
        if r["mode"] == "extractive":
            self.assertIn("本地检索摘要模式", r["answer"])
            self.assertIn("离线", r["warnings"][0])


if __name__ == "__main__":
    unittest.main()
