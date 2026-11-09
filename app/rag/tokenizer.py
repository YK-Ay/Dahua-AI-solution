"""零依赖中文分词：不装 jieba / 不下载模型，也能兼顾「型号精确」和「中文语义」。

思路很朴素：
  - 英文/数字/型号（DH-IPC-HFW2649M-A-LED）：整串当 1 个 token，再按 - / . / _ 拆子段
  - 中文：切成 2 字片段（bigram）+ 单字，能容忍「摄像机 / 摄像头」这类字面差异
  - 关键词路用全部 token（擅长精确型号）；语义路只用中文 token（擅长近似表达）

这一层是「本地轻量语义近似」，不是真 embedding。将来接真向量模型时，
只需替换 index.py 里的 vector_recall()，其余代码不动。
"""
from __future__ import annotations

import re
import unicodedata

LATIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/\-]{1,48}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
SPLIT_RE = re.compile(r"[-._/\s]+")

STOP_CHARS = set("的了和与或在是有为对中及等之其该这那并就把从以也很更最一个")
STOP_BIGRAMS = {
    "我们", "可以", "进行", "以及", "什么", "怎么", "如何", "哪些", "需要",
    "这个", "那个", "一个", "支持", "使用", "通过", "采用", "实现", "提供",
    # 通用问法词：几乎每篇资料都有，对语义路没有区分度
    "参数", "价格", "型号", "多少", "推荐", "详细", "介绍", "请问", "一下",
    "哪些", "询价", "报价", "配置", "方案", "产品", "是否", "关于",
}


def _normalize(text: str) -> str:
    # 全角转半角，避免「ＤＨ－ＩＰＣ」这类字符影响型号匹配
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def keyword_tokens(text: str) -> list[str]:
    """关键词路：型号/英文/数字 + 中文 bigram + 中文单字。"""
    s = _normalize(text)
    out: list[str] = []
    for m in LATIN_RE.finditer(s):
        raw = m.group(0).strip("-._/")
        if len(raw) < 2:
            continue
        out.append(raw)
        for piece in SPLIT_RE.split(raw):
            if len(piece) >= 2:
                out.append(piece)
    for m in CJK_RE.finditer(s):
        seg = m.group(0)
        for ch in seg:
            if ch not in STOP_CHARS:
                out.append(ch)
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in STOP_BIGRAMS:
                out.append(bg)
    return out


def semantic_tokens(text: str) -> list[str]:
    """语义近似路：只保留「实词 bigram」，与关键词路互补。

    不收单字、不收含虚词的碎片（如「数和」「和价」）——这类碎片很稀有、
    idf 反而高，会把「参数和价格」这种泛问句误匹配到一堆无关产品。
    """
    s = _normalize(text)
    out: list[str] = []
    for m in CJK_RE.finditer(s):
        seg = m.group(0)
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg in STOP_BIGRAMS:
                continue
            if any(ch in STOP_CHARS for ch in bg):
                continue
            out.append(bg)
    return out


def query_terms(text: str) -> set[str]:
    """用于「至少命中几个词」的兜底判定：去重后的关键词集合。"""
    return {t for t in keyword_tokens(text) if len(t) >= 2}
