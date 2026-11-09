"""本轮优化回归：字段别名 / 归一化、竞品文档不截断、Word 来源清单。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compare import (  # noqa: E402
    FIELDS, p_fiber_ports, p_managed, p_poe_ports, p_watt,
)
from app.rag.sources import load_docs  # noqa: E402
from config.settings import DB_PATH  # noqa: E402


class FieldAliasTest(unittest.TestCase):
    def test_managed_recognizes_web_and_client(self):
        self.assertEqual(p_managed("Web，4200客户端，海康互联App").num, 1.0)
        self.assertEqual(p_managed("支持WEB管理").num, 1.0)
        self.assertEqual(p_managed("非网管").num, 0.0)
        self.assertIsNone(p_managed("核心交换机标准C类业务板卡").num)

    def test_poe_ports_recognizes_range_and_count(self):
        self.assertEqual(p_poe_ports("端口1～24").num, 24.0)
        self.assertEqual(p_poe_ports("8个千兆PoE电口，2个千兆光口").num, 8.0)

    def test_fiber_ports_prefers_base_x(self):
        self.assertEqual(
            p_fiber_ports("8个10/100/1000Base-T以太网端口，2个1000Base-X以太网端口").num,
            2.0,
        )

    def test_watt_multi_caliber_takes_max_with_note(self):
        r = p_watt("≤80W;≤50W;≤200W")
        self.assertEqual(r.num, 200.0)
        self.assertIn("多个功率口径", r.note)

    def test_poe_category_has_expanded_aliases(self):
        hk = {a for s in FIELDS["PoE交换机"] for a in s["hk"]}
        for alias in ("端口", "管理方式", "管理维护", "POE输出功率", "端口最大供电功率"):
            self.assertIn(alias, hk)


class ProductPoolTest(unittest.TestCase):
    def test_dahua_pool_recommends_representative_first(self):
        from app.compare_service import OFFICIAL_PAIRS, products
        for cat, (rep, _hk) in OFFICIAL_PAIRS.items():
            data = products(cat, "大华", limit=5)
            self.assertEqual(data["items"][0]["model"], rep)
            self.assertIn("推荐", data["items"][0]["fact_note"])

    def test_alignment_scores_are_descending(self):
        from app.compare_service import products
        data = products("PoE交换机", "大华", limit=20)
        scores = [x["match_score"] for x in data["items"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIn("可对齐", data["items"][1]["fact_note"])

    def test_switch_pool_excludes_cameras(self):
        # 旧过滤里 '%网络%' 会把「网络摄像机」混进交换机池
        import sqlite3
        from app.compare_service import CATEGORY_FILTERS, products
        data = products("PoE交换机", "大华", limit=60)
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            bad = [x["model"] for x in data["items"]
                   if con.execute("SELECT 1 FROM products WHERE model=? AND class_name LIKE '%摄像机%'",
                                  (x["model"],)).fetchone()]
        finally:
            con.close()
        self.assertFalse(bad, f"交换机池里混入了摄像机：{bad[:3]}")
        self.assertLess(data["total"], 600)


class RagSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = load_docs(Path(DB_PATH))
        cls.cf = [d for d in cls.docs if d.subset == "competitor_fact"]

    def test_no_competitor_doc_exceeds_prompt_limit(self):
        self.assertTrue(self.cf)
        self.assertLessEqual(max(len(d.text) for d in self.cf), 1200)

    def test_long_model_keeps_all_facts(self):
        # 海康参数最多的机型字段数最多，旧实现 lines[:80] 会截断丢字段；
        # 按分区细拆后，同一型号的全部字段应完整保留、切成多篇（>1）小文档，
        # 且细拆后的总字段数应等于竞品表该型号的实际参数条数。
        import sqlite3
        from config.settings import DB_PATH
        # 找出竞品参数文档里「总字段数」最多的型号
        by_model: dict[str, list] = {}
        for d in self.cf:
            by_model.setdefault(d.meta.get("model"), []).append(d)
        model = max(by_model, key=lambda m: sum(x.meta["field_count"] for x in by_model[m]))
        parts = by_model[model]
        total_facts = sum(d.meta["field_count"] for d in parts)
        self.assertGreater(total_facts, 60, f"{model} 参数过少，无法验证细拆")
        self.assertGreater(len(parts), 1, f"{model} 应被切成多篇文档")
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        real = con.execute(
            "SELECT COUNT(*) FROM competitor_facts WHERE brand='海康' AND model=?",
            (model,)).fetchone()[0]
        con.close()
        self.assertEqual(total_facts, real, "细拆后字段数与竞品表实际条数不一致")

    def test_split_docs_are_finer_than_models(self):
        import sqlite3
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        n = con.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM competitor_facts GROUP BY brand, model)"
        ).fetchone()[0]
        con.close()
        self.assertGreater(len(self.cf), n)


if __name__ == "__main__":
    unittest.main()
