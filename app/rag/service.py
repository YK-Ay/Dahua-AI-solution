"""RAG 对外唯一入口：ask() / status() / reindex()。

一次提问的完整生命周期（也是给人讲架构时最值得画的一条线）：
  用户问题
   → 模式/行业路由（决定能搜哪些子集，防竞品资料串味）
   → 双路召回（BM25 + 本地语义近似）→ RRF 融合 → 降级兜底
   → 命中资料编号成 [资料N]，拼反幻觉 prompt
   → 有 Key 走大模型 / 没 Key 走检索摘要
   → 后置校验型号·金额·引用编号
   → 答案 + 可点击来源卡片 + 警告 + 免责声明
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from config.settings import DB_PATH

from .. import netcheck
from . import config as C
from .generator import generate
from .index import RagIndex
from .prompt import source_cards
from .sources import docs_stats, load_docs

_lock = threading.Lock()
_index: RagIndex | None = None
_index_mtime: float = 0.0
_index_built_at: str = ""
_index_seconds: float = 0.0


def _ensure_index(force: bool = False) -> RagIndex:
    global _index, _index_mtime, _index_built_at, _index_seconds
    mtime = Path(DB_PATH).stat().st_mtime
    with _lock:
        if force or _index is None or mtime != _index_mtime:
            t0 = time.time()
            docs = load_docs(Path(DB_PATH))
            _index = RagIndex(docs)
            _index_mtime = mtime
            _index_seconds = round(time.time() - t0, 2)
            _index_built_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return _index


def reindex() -> dict:
    idx = _ensure_index(force=True)
    return {"ok": True, **docs_stats(idx.docs), "build_seconds": _index_seconds}


def status() -> dict:
    idx = _ensure_index()
    online = netcheck.llm_reachable(C.DEEPSEEK_BASE_URL)
    llm_ready = bool(C.DEEPSEEK_API_KEY) and online
    if llm_ready:
        mode = "大模型生成"
    elif not online:
        mode = "离线检索摘要（无网络，本地能力完整可用）"
    else:
        mode = "检索摘要（零 Key 降级）"
    return {
        "ok": True,
        **docs_stats(idx.docs),
        "llm_enabled": llm_ready,
        "network_online": online,
        "offline": not online,
        "llm_model": C.DEEPSEEK_MODEL if llm_ready else "",
        "mode": mode,
        "engine": {
            "keyword": "BM25（型号/中文 bigram）",
            "vector": "本地中文 bigram idf 余弦近似（可替换为真 embedding）",
            "fusion": f"RRF alpha={C.RRF_ALPHA}, k={C.RRF_K}",
            "fallback": "向量异常自动降级纯关键词；行业过滤为空自动放宽",
        },
        "built_at": _index_built_at,
        "build_seconds": _index_seconds,
        "subsets_by_mode": C.SUBSETS,
    }


def ask(query: str, mode: str = "solution", industry: str = "", top_k: int | None = None) -> dict:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "问题不能为空"}
    if mode not in C.SUBSETS:
        mode = "solution"

    idx = _ensure_index()
    subsets = C.SUBSETS[mode]
    found = idx.search(query, subsets, industry=industry, top_k=top_k)
    hits = found["hits"]
    # 资料编号在服务层统一分配：大模型路径与抽取式降级路径都必须有 [资料N]
    for i, h in enumerate(hits, 1):
        h.n = i
    gen = generate(query, hits, mode)
    warnings = list(gen["warnings"])
    if found["engine"].get("low_relevance"):
        warnings.insert(0, "检索相关性偏弱：资料可能无法支撑该问题，请重点核对『待核实项』")
    if found["engine"].get("industry_relaxed"):
        warnings.insert(0, f"按行业「{industry}」过滤后资料不足，已自动放宽到全部子集")

    return {
        "ok": True,
        "query": query,
        "answer": gen["answer"],
        "mode": gen["mode"],                 # llm / extractive / no_evidence
        "engine": found["engine"],
        "sources": source_cards(hits),
        "warnings": warnings,
        "llm_error": gen["llm_error"],
        "disclaimer": C.DISCLAIMER,
        "filters": {"mode": mode, "industry": industry, "subsets": subsets,
                    "top_k": top_k or C.DEFAULT_TOP_K},
    }
