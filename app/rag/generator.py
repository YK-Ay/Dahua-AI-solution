"""生成层：有 Key 走大模型，没 Key 走「检索摘要」，任何情况下都不编数字。

两道防线：
  1. 前置：prompt.py 的硬约束 + 只把检索到的资料塞进上下文
  2. 后置：verify_answer() 扫描回答里的 [资料N]、型号、金额，
     凡是检索资料里逐字找不到的，统一列进 warnings 让界面标黄
"""
from __future__ import annotations

import re

import requests

from .. import netcheck
from . import config as C
from .index import Hit
from .prompt import SYSTEM_PROMPT, build_user_prompt

MODEL_RE = re.compile(r"\b(?:DH|DHI|PFM)-[A-Za-z0-9][A-Za-z0-9\-/\.]{2,32}", re.I)
REF_RE = re.compile(r"\[资料(\d+)\]")
MONEY_RE = re.compile(r"¥\s?([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*元")


def verify_answer(answer: str, hits: list[Hit]) -> list[str]:
    """后置校验：找出回答里「资料里没有」的型号/金额/引用编号。"""
    warnings: list[str] = []
    corpus = "\n".join(h.doc.text for h in hits)
    valid_n = {h.n for h in hits}

    refs = {int(x) for x in REF_RE.findall(answer)}
    bad_refs = sorted(refs - valid_n)
    if bad_refs:
        warnings.append(f"引用了不存在的资料编号：{', '.join('[资料%d]' % n for n in bad_refs)}")
    if hits and not refs:
        warnings.append("回答里没有出现任何 [资料N] 引用，溯源不完整")

    models = {m.upper() for m in MODEL_RE.findall(answer)}
    unknown = [m for m in sorted(models) if m.upper() not in corpus.upper()]
    if unknown:
        warnings.append("以下型号未在检索资料中逐字出现，需人工核实：" + "、".join(unknown[:8]))

    nums = set()
    for a, b in MONEY_RE.findall(answer):
        raw = (a or b).replace(",", "")
        try:
            nums.add(f"{float(raw):.2f}")
            nums.add(f"{float(raw):.0f}")
        except ValueError:
            pass
    corpus_digits = re.sub(r"[^\d.]", "", corpus)
    bad_money = sorted(n for n in nums if n not in corpus_digits and n.rstrip("0").rstrip(".") not in corpus_digits)
    if bad_money and len(bad_money) <= 6:
        warnings.append("以下金额数字未在检索资料中出现，需人工核实：" + "、".join(bad_money))
    return warnings


def _evidence_lines(text: str, query: str, limit: int = 5) -> list[str]:
    """从资料原文里挑最相关的几行，绝不改写。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    terms = [t for t in re.findall(r"[A-Za-z0-9\-]{3,}|[\u4e00-\u9fff]{2,}", query) if len(t) >= 2]
    def score(ln: str) -> int:
        s = sum(2 for t in terms if t.lower() in ln.lower())
        if re.search(r"型号|单价|¥|价格", ln):
            s += 2
        return s
    picked = sorted(lines, key=lambda ln: -score(ln))[:limit]
    # 保持原顺序，读起来更像原文
    return [ln for ln in lines if ln in picked][:limit]


def extractive_answer(query: str, hits: list[Hit], reason: str = "") -> str:
    """离线 / 零 Key 降级：只做「带编号的证据摘录」，不生成任何新数字。"""
    why = reason or "未配置 `DEEPSEEK_API_KEY`"
    head = (
        f"⚙️ **本地检索摘要模式**（{why}，本次没有调用大模型）\n\n"
        "下面每一句都是从资料里原样摘出的片段，未做生成、未补数字、未做推算。"
        "全部检索与摘录都在本机完成，离线可用；恢复联网后会自动切换成完整方案生成。\n\n"
        f"检索问题：{query}\n\n---\n\n"
    )
    blocks = []
    for h in hits:
        d = h.doc
        lines = _evidence_lines(d.text, query)
        body = "\n".join(f"> {ln} [资料{h.n}]" for ln in lines) if lines else f"> {h.snippet} [资料{h.n}]"
        blocks.append(
            f"### [资料{h.n}] {d.title}\n"
            f"来源：{d.source or d.label} ｜ 行业：{d.industry or '未标注'} ｜ 类型：{d.label}\n\n{body}"
        )
    tail = (
        "\n\n---\n\n**待核实项**\n"
        "- 以上仅为检索到的原文片段，未覆盖的内容需进一步核实。\n"
        "- 配单资料里同一分类下的多个型号是「可替换候选」，应择一选型，不能累加成总价。\n"
        "- " + C.DISCLAIMER
    )
    return head + "\n\n".join(blocks) + tail


def llm_answer(query: str, hits: list[Hit], mode: str) -> str:
    """DeepSeek（OpenAI 兼容接口）；任何异常都抛出去，由 service 决定降级。"""
    payload = {
        "model": C.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(query, hits, mode)},
        ],
        "temperature": 0.2,
        "max_tokens": 2200,
        "stream": False,
    }
    r = requests.post(
        C.DEEPSEEK_BASE_URL + "/chat/completions",
        headers={"Authorization": "Bearer " + C.DEEPSEEK_API_KEY,
                 "Content-Type": "application/json"},
        json=payload,
        timeout=C.LLM_TIMEOUT,
    )
    r.raise_for_status()
    j = r.json()
    text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not text.strip():
        raise RuntimeError("大模型返回空内容")
    return text.strip()


def generate(query: str, hits: list[Hit], mode: str) -> dict:
    """统一入口：返回 answer / mode / warnings / llm_error。"""
    if not hits:
        return {
            "answer": "资料未覆盖，需进一步核实。\n\n本次检索没有找到与问题相关的资料，"
                      "为避免编造，不给出配置建议和价格。可以换个说法、放宽行业筛选，"
                      "或先在知识库页补充资料。\n\n" + C.DISCLAIMER,
            "mode": "no_evidence",
            "warnings": ["检索无命中，已按反幻觉规则拒绝生成"],
            "llm_error": "",
        }

    llm_error = ""
    answer = ""
    used = "extractive"
    # 离线时不做 60 秒的无效等待：一次短探测后直接走本地摘录
    online = netcheck.llm_reachable(C.DEEPSEEK_BASE_URL)
    offline_reason = ""
    if not online:
        offline_reason = ("当前无网络连接（已自动离线降级）" if C.DEEPSEEK_API_KEY
                          else "当前无网络连接")
    elif not C.DEEPSEEK_API_KEY:
        offline_reason = ""

    if C.DEEPSEEK_API_KEY and online:
        try:
            answer = llm_answer(query, hits, mode)
            used = "llm"
        except Exception as exc:                  # noqa: BLE001 —— 降级是设计的一部分
            llm_error = f"{type(exc).__name__}: {exc}"[:300]
            offline_reason = "大模型调用失败，已自动降级"
    if not answer:
        answer = extractive_answer(query, hits, reason=offline_reason)

    warnings = verify_answer(answer, hits)
    if llm_error:
        warnings.insert(0, f"大模型调用失败已降级为检索摘要：{llm_error}")
    elif offline_reason:
        warnings.insert(0, f"离线模式：{offline_reason}，未调用大模型，答案全部来自本地资料")
    return {
        "answer": answer,
        "mode": used,
        "warnings": warnings,
        "llm_error": llm_error,
        "offline": not online,
    }
