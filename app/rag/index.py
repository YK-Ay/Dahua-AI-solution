"""混合检索：关键词 BM25 + 本地语义近似 → RRF 融合 → 失败自动降级。

对应《融入改造清单》Step 2/3：
  - 双路召回（关键词 top40 / 语义 top15），用 RRF 只按「排名」融合，
    不用分数加权 —— 因为两路分数量纲不可比，按排名更稳
  - 语义路出任何异常 → 自动降级纯关键词，永不空结果
  - 行业标签做硬过滤，但过滤后为空会自动放宽并在返回值里标注 relaxed

为什么语义路不是真向量：本机没有 embedding 模型也没 Key。这里用
「中文 bigram 的 idf 加权余弦」做字面近似，接口与真向量完全一致；
将来接真模型只需替换 vector_recall()，其他文件不用改。
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from . import config as C
from .sources import Doc
from .tokenizer import keyword_tokens, query_terms, semantic_tokens

K1, B = 1.5, 0.75
MODEL_QUERY_RE = re.compile(r"\b(?:DH|DHI|PFM|DS|ST)[-A-Z0-9]{4,}", re.I)


@dataclass
class Hit:
    doc: Doc
    score: float
    kw_rank: int | None = None
    vec_rank: int | None = None
    n: int = 0                    # [资料N] 的编号，由 service 层填
    snippet: str = ""


class RagIndex:
    def __init__(self, docs: list[Doc]):
        self.docs = docs
        self.n_docs = len(docs)
        self.kw_tf: list[Counter] = []
        self.sem_tf: list[Counter] = []
        self.kw_len: list[int] = []
        self.kw_post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.sem_post: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.df_kw: Counter = Counter()
        self.df_sem: Counter = Counter()
        self._build()

    # ---------- 建索引 ----------
    def _build(self) -> None:
        for i, d in enumerate(self.docs):
            blob = f"{d.title} {d.title} {d.text}"     # 标题加权：重复两次
            kw = Counter(keyword_tokens(blob))
            sm = Counter(semantic_tokens(blob))
            self.kw_tf.append(kw)
            self.sem_tf.append(sm)
            self.kw_len.append(max(1, sum(kw.values())))
            for t, f in kw.items():
                self.kw_post[t].append((i, f))
                self.df_kw[t] += 1
            for t in sm:
                self.df_sem[t] += 1
        self.avgdl = sum(self.kw_len) / max(1, self.n_docs)

        # 语义路：每篇文档一个 L2 归一化的稀疏向量，并建倒排便于查询累加
        for i, sm in enumerate(self.sem_tf):
            vec = {}
            for t, f in sm.items():
                w = (1 + math.log(f)) * self._idf_sem(t)
                vec[t] = w
            nrm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
            for t, w in vec.items():
                self.sem_post[t].append((i, w / nrm))

    def _idf_kw(self, t: str) -> float:
        return math.log(1 + (self.n_docs - self.df_kw[t] + 0.5) / (self.df_kw[t] + 0.5))

    def _idf_sem(self, t: str) -> float:
        return math.log(1 + (self.n_docs - self.df_sem[t] + 0.5) / (self.df_sem[t] + 0.5))

    # ---------- 一路：BM25 关键词 ----------
    def keyword_recall(self, query: str, allowed: set[int], topn: int) -> list[tuple[int, float]]:
        qterms = set(keyword_tokens(query))
        scores: dict[int, float] = defaultdict(float)
        for t in qterms:
            if t not in self.kw_post:
                continue
            idf = self._idf_kw(t)
            for i, tf in self.kw_post[t]:
                if i not in allowed:
                    continue
                dl = self.kw_len[i]
                scores[i] += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * dl / self.avgdl))
        return sorted(scores.items(), key=lambda x: -x[1])[:topn]

    # ---------- 二路：本地语义近似（可整体替换成真 embedding） ----------
    def vector_recall(self, query: str, allowed: set[int], topn: int) -> list[tuple[int, float]]:
        q = {}
        for t, f in Counter(semantic_tokens(query)).items():
            idf = self._idf_sem(t)
            if idf < C.MIN_SEM_IDF:       # 泛词（参数/价格/型号…）不参与语义路
                continue
            q[t] = (1 + math.log(f)) * idf
        if not q:
            # 问题里只剩泛词 → 语义路无有效信号，等价于「降级纯关键词」
            return []
        nrm = math.sqrt(sum(w * w for w in q.values())) or 1.0
        q = {t: w / nrm for t, w in q.items()}
        scores: dict[int, float] = defaultdict(float)
        for t, qw in q.items():
            for i, dw in self.sem_post.get(t, ()):
                if i in allowed:
                    scores[i] += qw * dw
        return sorted(((i, s) for i, s in scores.items() if s > 0), key=lambda x: -x[1])[:topn]

    # ---------- 行业过滤 ----------
    def _allowed(self, subsets: list[str], industry: str) -> tuple[set[int], bool]:
        wanted = set(subsets)
        base = {i for i, d in enumerate(self.docs) if d.subset in wanted}
        if not industry:
            return base, False
        alias = C.INDUSTRY_ALIASES.get(industry.strip(), industry.strip())
        if not alias:
            return base, False
        keys = [k for k in re.split(r"[/\s,，、]+", alias) if len(k) >= 2]
        strict = {
            i for i in base
            if any(k in (self.docs[i].industry + " " + self.docs[i].title) for k in keys)
        }
        # 硬过滤后太少 → 自动放宽，保证「永不空结果」，但如实标注
        if len(strict) >= 3:
            return strict, False
        return base, True

    # ---------- 对外：混合召回 ----------
    def search(
        self,
        query: str,
        subsets: list[str],
        industry: str = "",
        top_k: int | None = None,
    ) -> dict:
        top_k = top_k or C.DEFAULT_TOP_K
        allowed, relaxed = self._allowed(subsets, industry)

        kw = self.keyword_recall(query, allowed, C.KEYWORD_TOP)
        vector_failed = False
        try:
            vc = self.vector_recall(query, allowed, C.VECTOR_TOP)
        except Exception:                      # noqa: BLE001 —— 降级是设计的一部分
            vc, vector_failed = [], True

        # 查询里带具体型号时，以「型号精确匹配」的关键词路为主
        alpha = C.MODEL_QUERY_ALPHA if MODEL_QUERY_RE.search(query) else C.RRF_ALPHA

        # RRF：只用排名，不混用两路的原始分数
        fused: dict[int, float] = defaultdict(float)
        krank: dict[int, int] = {}
        vrank: dict[int, int] = {}
        for rank, (i, _) in enumerate(kw):
            krank[i] = rank + 1
            fused[i] += alpha / (C.RRF_K + rank + 1)
        for rank, (i, _) in enumerate(vc):
            vrank[i] = rank + 1
            fused[i] += (1 - alpha) / (C.RRF_K + rank + 1)

        hits: list[Hit] = []
        for i, score in fused.items():
            d = self.docs[i]
            # 两路都有结果时，只被一路召回的资料温和降权；
            # 若语义路整体为空（泛词查询/降级），不做惩罚，避免误伤关键词结果
            if kw and vc and (i not in krank or i not in vrank):
                score *= C.SINGLE_ROUTE_PENALTY
            # 子集权重：配单/方案优先级高，产品库降权避免刷屏
            final = score * C.SUBSET_BOOST.get(d.subset, 1.0)
            if final <= 0:
                continue
            hits.append(Hit(doc=d, score=round(final, 6),
                            kw_rank=krank.get(i), vec_rank=vrank.get(i)))
        hits.sort(key=lambda h: -h.score)
        hits = hits[:top_k]

        qterms = query_terms(query)
        for h in hits:
            text = h.doc.text
            # 片段：优先截取第一个查询词出现处，便于人工核对
            pos = min((text.find(t) for t in qterms if len(t) >= 2 and text.find(t) >= 0), default=-1)
            start = max(0, pos - 60) if pos >= 0 else 0
            h.snippet = text[start:start + 220].replace("\n", " ")

        return {
            "hits": hits,
            "engine": {
                "keyword_hits": len(kw),
                "vector_hits": len(vc),
                "vector_failed": vector_failed,
                "rrf_alpha": alpha,
                "rrf_k": C.RRF_K,
                "model_query": alpha != C.RRF_ALPHA,
                "subsets": subsets,
                "industry": industry,
                "industry_relaxed": relaxed,
                "candidates": len(allowed),
                "low_relevance": bool(hits) and hits[0].score < C.LOW_RELEVANCE,
                "single_route_penalty": C.SINGLE_ROUTE_PENALTY,
            },
        }
