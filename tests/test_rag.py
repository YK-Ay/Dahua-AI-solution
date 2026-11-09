"""RAG 层单测：检索子集隔离、RRF 降级、资料编号、反幻觉后置校验。

不联网、不调用大模型（测试里强制把 Key 清空走抽取式降级），
跑一次约 3 秒（建一次内存索引）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import DB_PATH  # noqa: E402
from app.rag import config as C  # noqa: E402
from app.rag import service as S  # noqa: E402
from app.rag.generator import generate, verify_answer  # noqa: E402
from app.rag.index import Hit, RagIndex  # noqa: E402
from app.rag.sources import Doc, load_docs  # noqa: E402
from app.rag.tokenizer import keyword_tokens, semantic_tokens  # noqa: E402


class RagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._key_backup = C.DEEPSEEK_API_KEY
        C.DEEPSEEK_API_KEY = ""          # 测试一律走离线抽取式
        cls.idx = RagIndex(load_docs(Path(DB_PATH)))
        cls.docs = cls.idx.docs

    @classmethod
    def tearDownClass(cls):
        C.DEEPSEEK_API_KEY = cls._key_backup

    # ---------- 分词 ----------
    def test_tokenizer_keeps_full_model_and_cjk_bigram(self):
        ts = keyword_tokens("DH-IPC-HFW2649M-A-LED 停车场抓拍")
        self.assertIn("dh-ipc-hfw2649m-a-led", ts)
        self.assertIn("dh", ts)
        self.assertIn("停车", ts)
        self.assertIn("场抓", ts)

    def test_generic_words_dropped_from_semantic_route(self):
        allowed = set(range(len(self.docs)))
        # 泛词查询在语义路被 idf 门槛过滤成「无有效信号」→ 等价于降级纯关键词
        self.assertEqual(self.idx.vector_recall("参数和价格", allowed, 15), [])
        ts = semantic_tokens("停车场出入口抓拍车牌")
        self.assertIn("停车", ts)
        self.assertIn("车牌", ts)

    # ---------- 索引 / RRF / 降级 ----------
    def test_index_stats_and_rrf_fields(self):
        r = self.idx.search("停车出入口抓拍一体机", C.SUBSETS["solution"], top_k=5)
        self.assertTrue(r["hits"])
        self.assertFalse(r["engine"]["vector_failed"])
        self.assertIn("rrf_alpha", r["engine"])
        for h in r["hits"]:
            self.assertGreater(h.score, 0)

    def test_model_query_prefers_keyword_route(self):
        r = self.idx.search("DH-IPC-HFW2649M-A-LED", C.SUBSETS["solution"], top_k=5)
        self.assertTrue(r["engine"]["model_query"])
        self.assertGreater(r["engine"]["rrf_alpha"], 0.7)
        # 型号查询不应被无关的「音频处理器」之类产品占满
        titles = " ".join(h.doc.title for h in r["hits"])
        self.assertNotIn("机架式音频处理器", titles)

    def test_vector_failure_degrades_to_keyword(self):
        original = self.idx.vector_recall

        def boom(*a, **kw):
            raise RuntimeError("模拟向量服务异常")

        self.idx.vector_recall = boom
        try:
            r = self.idx.search("停车出入口抓拍", C.SUBSETS["solution"], top_k=5)
        finally:
            self.idx.vector_recall = original
        self.assertTrue(r["engine"]["vector_failed"])
        self.assertEqual(r["engine"]["vector_hits"], 0)
        self.assertTrue(r["hits"], "向量挂了也必须返回结果")

    # ---------- 子集隔离 ----------
    def test_subset_isolation(self):
        a = self.idx.search("停车抓拍设备", C.SUBSETS["solution"], top_k=8)
        b = self.idx.search("停车抓拍设备", C.SUBSETS["competitor"], top_k=8)
        sa = {h.doc.subset for h in a["hits"]}
        sb = {h.doc.subset for h in b["hits"]}
        self.assertFalse(sa & {"competitor_solution", "competitor_fact"})
        self.assertFalse(sb & {"dahua_solution", "bom", "product"})

    def test_industry_filter_relaxes_when_too_few(self):
        r = self.idx.search("量子计算机机房", C.SUBSETS["solution"], industry="不存在行业", top_k=3)
        self.assertTrue(r["engine"]["industry_relaxed"])
        self.assertTrue(r["hits"])

    # ---------- 配单语义 ----------
    def test_bom_doc_warns_about_candidate_pool(self):
        bom = [d for d in self.docs if d.subset == "bom"]
        self.assertTrue(bom)
        self.assertIn("可替换候选", bom[0].text)
        self.assertIn("口径", bom[0].text)

    # ---------- 生成 / 反幻觉 ----------
    def test_extractive_numbers_sources_from_one(self):
        r = S.ask("小区出入口抓拍一体机有哪些候选型号？", mode="solution", top_k=3)
        self.assertEqual(r["mode"], "extractive")
        self.assertIn("[资料1]", r["answer"])
        self.assertNotIn("[资料0]", r["answer"])
        self.assertEqual([s["n"] for s in r["sources"]], [1, 2, 3])
        for s in r["sources"]:
            self.assertIn("[资料%d]" % s["n"], r["answer"])

    def test_no_evidence_refuses_to_invent(self):
        r = generate("随便什么", [], "solution")
        self.assertEqual(r["mode"], "no_evidence")
        self.assertIn("资料未覆盖", r["answer"])
        self.assertIn("不给出配置建议", r["answer"])

    def test_verify_flags_unknown_model_and_bad_ref(self):
        hits = [Hit(doc=Doc(doc_id="x", title="测试", text="型号 DH-IPC-REAL 单价 ¥618.00",
                            subset="bom", source="测试"), score=1.0, n=1)]
        warnings = verify_answer("推荐 DH-IPC-FAKE-999，价格 ¥99999.00 [资料1][资料9]", hits)
        joined = " ".join(warnings)
        self.assertIn("DH-IPC-FAKE-999", joined)
        self.assertIn("[资料9]", joined)

    def test_source_cards_carry_traceable_fields(self):
        r = S.ask("停车抓拍一体机报价", mode="solution", top_k=2)
        for s in r["sources"]:
            self.assertTrue(s["doc_id"])
            self.assertTrue(s["label"])
            self.assertIn("snippet", s)
            self.assertIn("score", s)

    def test_disclaimer_present(self):
        r = S.ask("小区监控配置", mode="solution", top_k=2)
        self.assertIn("人工核对", r["disclaimer"])


if __name__ == "__main__":
    unittest.main()
