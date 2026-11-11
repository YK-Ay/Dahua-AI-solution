"""RAG 选型：让「真实配单 + 中标明细」参与摄像机选型，而不是拍脑袋定型号。

三层结构（对应售前选型 Agent 的确定性内核）：
  1. 离线蒸馏：把 53 份云商配单、中标明细按「场景族 × 行业 × 型号」计数，
     得到「什么场景、哪些真实方案选了什么型号」的规则表。
  2. 在线召回：复用 app.rag 的 RagIndex（BM25 + 中文 bigram 语义近似），
     在案例文档上召回与当前场景最像的真实案例。
  3. 决策与校验：规则表票数 + 召回相似度加权投票 → 产品库硬校验
     （类目 / 标准价 / 形态 / 反场景词）→ 可选 LLM 复排 → 输出证据链。

设计原则：
- LLM 只在「候选型号之间」做选择，不负责编造型号；候选必须来自产品库。
- 离线 / 无 Key / 任何异常都退化成确定性投票，不阻塞主流程。
- 每个结论都能回溯到具体方案名或中标公告名，写进 Device.reason 供人工核对。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections import defaultdict

import requests

from app import netcheck
from app.rag import config as C
from app.rag.index import RagIndex
from app.rag.sources import Doc

# 场景族：keys 用于把配单分类 / 中标采购项映射到售前场景；
# shape 是该场景的默认摄像机形态；avoid 是明显不属于该场景的产品词。
SCENE_FAMILIES: dict[str, dict] = {
    "周界围墙": {
        "keys": ["周界", "围墙", "边界", "警戒"],
        "shape": "枪型",
        "avoid": ["高空抛物", "客流", "热成像", "测温", "防爆", "门禁", "电梯"],
    },
    "出入口": {
        "keys": ["大门", "出入口", "门禁", "通道", "闸机", "门口", "单元门"],
        "shape": "枪型",
        "avoid": ["客流", "热成像", "测温", "防爆", "高空抛物", "电梯", "电瓶车"],
    },
    "主干道": {
        "keys": ["道路", "车道", "卡口", "路面", "测速", "抓拍", "车辆识别"],
        "shape": "球机",
        "avoid": ["客流", "热成像", "测温", "防爆", "电梯", "高空抛物"],
    },
    "电梯轿厢": {
        "keys": ["电梯", "轿厢", "入梯", "电瓶车"],
        "shape": "半球",
        "avoid": ["枪型", "球机", "客流", "高空抛物", "热成像", "测温"],
    },
    "室内区域": {
        "keys": ["室内", "走廊", "大厅", "教室", "办公", "半球", "海螺", "会议", "宿舍"],
        "shape": "半球",
        "avoid": ["枪型", "球机", "高空抛物", "热成像", "测温", "防爆", "电梯"],
    },
    "停车场": {
        "keys": ["停车", "车位", "车库", "地库", "车棚"],
        "shape": "球机",
        "avoid": ["客流", "热成像", "测温", "防爆", "电梯", "高空抛物"],
    },
    "公共区域": {
        "keys": ["广场", "公共区域", "园区", "操场", "全景", "室外"],
        "shape": "",
        "avoid": ["客流", "热成像", "测温", "防爆", "电梯", "高空抛物"],
    },
}

CAMERA_CLASSES = ("网络摄像机", "分销球机", "球机", "全景系列网络摄像机", "项目型网络摄像机")

# 与摄像机场景无关的专用机型：无论哪个场景都不应被当成通用监控相机
NOISE_WORDS = ("火焰", "通道占用", "离岗", "消防")

# 规则票权重：真实配单/中标项目 > 单条明细
SCHEME_WEIGHT = 2.0
ITEM_WEIGHT = 0.5
TENDER_WEIGHT = 2.5
RETRIEVAL_WEIGHT = 1.2
KEYWORD_BONUS = 0.4

_llm_lock = threading.Lock()
_index_lock = threading.Lock()
_cache: dict[tuple[str, float], "SelectionIndex"] = {}


def family_of(text: str) -> str:
    """把任意分类名/采购项名映射到场景族；按 SCENE_FAMILIES 顺序取第一个命中。"""
    text = text or ""
    for fam, spec in SCENE_FAMILIES.items():
        if any(k in text for k in spec["keys"]):
            return fam
    return ""


def _db_key(conn: sqlite3.Connection) -> tuple[str, float]:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = (row[2] if row else "") or ""
    try:
        import os
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return path, mtime


class SelectionIndex:
    """一次建好、进程内复用；数据库文件变化时按 mtime 自动重建。"""

    def __init__(self, conn: sqlite3.Connection):
        self.products: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT model, display_name, class_name, price_stand, year_new FROM products "
            "WHERE class_name IN (" + ",".join("?" * len(CAMERA_CLASSES)) + ")",
            CAMERA_CLASSES,
        ):
            self.products[r["model"]] = dict(r)

        self.rule: dict[str, dict[str, dict[str, dict]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self.tender_rule: dict[str, dict[str, dict[str, dict]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self.docs: list[Doc] = []
        self.case_count = 0

        self._load_boms(conn)
        self._load_tenders(conn)
        self.idx = RagIndex(self.docs)

    # ---------- 建索引 ----------
    def _add(self, table, fam: str, industry: str, model: str,
             source: str, qty: float | None) -> None:
        ent = table[fam][industry].get(model)
        if ent is None:
            ent = {"sources": set(), "items": 0, "qty": 0.0}
            table[fam][industry][model] = ent
        ent["sources"].add(source)
        ent["items"] += 1
        ent["qty"] += float(qty or 0)

    def _load_boms(self, conn: sqlite3.Connection) -> None:
        for r in conn.execute(
            """SELECT bi.id, bi.category, bi.role, bi.product_name, bi.model,
                      bi.spec, bi.qty, b.scheme_name, b.industry
               FROM bom_items bi JOIN boms b ON b.scheme_id = bi.scheme_id
               WHERE bi.model IS NOT NULL AND bi.model != ''"""
        ):
            fam = family_of(f"{r['category']} {r['role']}")
            if not fam:
                continue
            self.case_count += 1
            if r["model"] in self.products:
                self._add(self.rule, fam, r["industry"], r["model"],
                          r["scheme_name"], r["qty"])
            self.docs.append(Doc(
                doc_id=f"bom:{r['id']}",
                title=f"{r['scheme_name']} · {r['category']} · {r['product_name']}",
                text=(f"场景：{fam}\n方案：{r['scheme_name']}\n行业：{r['industry']}\n"
                      f"配单分类：{r['category']}\n职责：{r['role']}\n"
                      f"产品：{r['product_name']}\n型号：{r['model']}\n规格：{r['spec']}"),
                subset="bom",
                industry=r["industry"],
                source=r["scheme_name"],
                meta={"family": fam, "model": r["model"], "category": r["category"]},
            ))

    def _load_tenders(self, conn: sqlite3.Connection) -> None:
        try:
            rows = conn.execute(
                """SELECT ti.id, ti.item_name, ti.model, ti.qty, ti.unit_price,
                          t.title, t.industry
                   FROM tender_items ti JOIN tenders t ON t.tender_id = ti.tender_id
                   WHERE ti.model IS NOT NULL AND ti.model != ''"""
            ).fetchall()
        except sqlite3.OperationalError:
            return
        for r in rows:
            fam = family_of(r["item_name"] or "")
            if not fam:
                continue
            if r["model"] in self.products:
                self._add(self.tender_rule, fam, r["industry"], r["model"],
                          r["title"], r["qty"])
            self.docs.append(Doc(
                doc_id=f"tender:{r['id']}",
                title=f"{r['title']} · {r['item_name']} · {r['model']}",
                text=(f"场景：{fam}\n中标项目：{r['title']}\n行业：{r['industry']}\n"
                      f"采购项：{r['item_name']}\n型号：{r['model']}\n"
                      f"数量：{r['qty']}\n单价：{r['unit_price']}"),
                subset="tender",
                industry=r["industry"],
                source=r["title"],
                meta={"family": fam, "model": r["model"], "category": r["item_name"]},
            ))

    # ---------- 候选打分 ----------
    def _rule_scores(self, family: str, industries: list[str]) -> dict[str, float]:
        scores: dict[str, float] = defaultdict(float)
        for table, weight in ((self.rule, 1.0), (self.tender_rule, 1.0)):
            for industry, models in table.get(family, {}).items():
                bonus = 1.5 if industry in industries else 1.0
                for model, ent in models.items():
                    base = (SCHEME_WEIGHT * len(ent["sources"])
                            + ITEM_WEIGHT * ent["items"])
                    if table is self.tender_rule:
                        base = TENDER_WEIGHT * len(ent["sources"])
                    scores[model] += base * bonus * weight
        return scores

    def _eligible(self, model: str, spec: dict, classes: list[str]) -> dict | None:
        p = self.products.get(model)
        if not p or p["class_name"] not in classes:
            return None
        if p["price_stand"] is None:
            return None
        name = p["display_name"] or ""
        if any(k in name for k in NOISE_WORDS + tuple(spec.get("avoid", ()))):
            return None
        if spec.get("shape") and spec["shape"] not in name:
            return None
        return p

    def select(
        self,
        scene: str,
        intent: str = "",
        industries: list[str] | None = None,
        classes: list[str] | None = None,
        keywords: list[str] | None = None,
        use_llm: bool | None = None,
    ) -> dict | None:
        """返回 {product, refs, basis, evidence, engine}；无可靠候选时返回 None。"""
        family = scene if scene in SCENE_FAMILIES else family_of(scene)
        if not family:
            return None
        spec = SCENE_FAMILIES[family]
        industries = [i for i in (industries or []) if i]
        classes = list(classes or ["网络摄像机"])
        keywords = list(keywords or [])

        scores = self._rule_scores(family, industries)

        found = self.idx.search(
            " ".join([family, intent] + spec["keys"]),
            ["bom", "tender"],
            industry="/".join(industries),
            top_k=30,
        )
        hits = [h for h in found["hits"] if h.doc.meta.get("family") == family]
        top = hits[0].score if hits else 0.0
        for h in hits:
            m = h.doc.meta.get("model", "")
            if m in self.products:
                scores[m] += RETRIEVAL_WEIGHT * (h.score / top if top else 0)

        # 硬校验：类目 + 标准价 + 形态 + 反场景词；全部不满足时逐级放宽
        shape_relaxed = avoid_relaxed = False
        ranked: list[tuple[float, dict]] = []
        for relax_shape, relax_avoid in ((False, False), (True, False), (True, True)):
            spec_try = dict(spec)
            if relax_shape:
                spec_try["shape"] = ""
            if relax_avoid:
                spec_try["avoid"] = []
            ranked = []
            for model, score in scores.items():
                p = self._eligible(model, spec_try, classes)
                if not p:
                    continue
                bonus = KEYWORD_BONUS * sum(1 for k in keywords if k in (p["display_name"] or ""))
                ranked.append((score + bonus, p))
            shape_relaxed, avoid_relaxed = relax_shape, relax_avoid
            if ranked:
                break
        if not ranked:
            return None

        ranked.sort(key=lambda x: (-x[0], x[1]["price_stand"]))
        cands = [p for _, p in ranked[:5]]

        llm_pick = None
        if use_llm is None:
            use_llm = C.SELECTION_LLM
        if use_llm:
            llm_pick = self._llm_choose(family, intent, cands)
        best = next((p for p in cands if p["model"] == llm_pick), cands[0])

        # 证据链：把该型号在同场景所有行业里的来源方案/中标项目都收进来
        sources: set[str] = set()
        for table in (self.rule, self.tender_rule):
            for _ind, models in table.get(family, {}).items():
                if best["model"] in models:
                    sources |= models[best["model"]]["sources"]
        refs = len(sources)
        if refs:
            basis = (f"RAG 参考 {refs} 个真实方案/中标项目，"
                     f"{family}场景选用 {best['model']}")
        else:
            basis = f"RAG 召回 {len(hits)} 个相似案例，{family}场景按相似度选出该型号"

        return {
            "product": best,
            "refs": refs,
            "basis": basis,
            "evidence": sorted(sources)[:3],
            "engine": {
                "family": family,
                "docs": len(self.docs),
                "hits": len(hits),
                "candidates": len(cands),
                "shape_relaxed": shape_relaxed,
                "avoid_relaxed": avoid_relaxed,
                "llm": bool(llm_pick),
            },
        }

    # ---------- 可选 LLM 复排 ----------
    def _llm_choose(self, family: str, intent: str, cands: list[dict]) -> str | None:
        """在 RAG 候选里让 LLM 复排；失败/超时/幻觉一律返回 None（回退规则票）。"""
        if not C.DEEPSEEK_API_KEY or not netcheck.llm_reachable(C.DEEPSEEK_BASE_URL):
            return None
        lines = [
            f"{i}. {p['model']}｜{p['display_name']}｜标准价 {p['price_stand']} 元"
            for i, p in enumerate(cands, 1)
        ]
        messages = [
            {"role": "system",
             "content": ("你是大华安防售前选型专家。只能从给定候选型号中选一个最贴合"
                         "场景与建设意图的，不得编造新型号。只输出 JSON：{\"model\": \"...\"}")},
            {"role": "user",
             "content": f"场景：{family}\n建设意图：{intent or '视频监控'}\n候选：\n"
                        + "\n".join(lines)},
        ]
        try:
            with _llm_lock:
                r = requests.post(
                    C.DEEPSEEK_BASE_URL + "/chat/completions",
                    headers={"Authorization": "Bearer " + C.DEEPSEEK_API_KEY,
                             "Content-Type": "application/json"},
                    json={"model": C.DEEPSEEK_MODEL, "messages": messages,
                          "temperature": 0, "max_tokens": 120, "stream": False},
                    timeout=15,
                )
            r.raise_for_status()
            text = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
            m = re.search(r"\{.*\}", text or "", re.S)
            picked = json.loads(m.group(0)).get("model") if m else None
        except Exception:  # noqa: BLE001 —— 复排失败不影响确定性结果
            return None
        return picked if any(p["model"] == picked for p in cands) else None


def get_index(repo) -> SelectionIndex:
    key = _db_key(repo.conn)
    with _index_lock:
        idx = _cache.get(key)
        if idx is None:
            idx = SelectionIndex(repo.conn)
            _cache.clear()
            _cache[key] = idx
        return idx


def select_camera(
    repo,
    scene: str,
    intent: str = "",
    industries: list[str] | None = None,
    classes: list[str] | None = None,
    keywords: list[str] | None = None,
    use_llm: bool | None = None,
) -> dict | None:
    """供 builder 调用的单场景选型入口；任何异常都返回 None 交给原有规则兜底。"""
    try:
        return get_index(repo).select(scene, intent, industries, classes,
                                      keywords, use_llm=use_llm)
    except Exception:  # noqa: BLE001 —— RAG 不可用时绝不拖垮主流程
        return None
