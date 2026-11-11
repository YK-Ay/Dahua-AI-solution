"""需求解析层：LLM 只做语义理解，数字一律不产，断网自动降级。

硬性口径（与项目反幻觉原则一致）：
- LLM **不允许产出任何数字**（点位数 / 存储天数 / 码流 / 价格）。这些仍由
  app.builder 的正则与 app.calc 的确定性函数处理；追问改写里出现数字即整批作废。
- LLM 只能从 SCENE_RULES 的固定白名单里选场景，不得编造场景或行业。
- 归一化出来的场景词只在「规则引擎没认清需求」时补写进原文，且补写的
  只是场景名词，不含任何数字；补写后重新跑 match_scenes 得到真正生效的场景。
- LLM 判断的行业只作为建议展示，不写回原文、不改动 detect_industry 口径。
- 有 Key + 有网才调 LLM；无网 / 无 Key / 调用失败 / 超时，一律回退原文 + 规则
  引擎 / 本地追问模板，功能完全可用。

这样 Agent 化不改变任何计算口径，只是让「地下车库」「宿舍楼」这类口语
能被归一化成规则引擎认识的场景词。
"""
from __future__ import annotations

import json
import os
import re
import threading

import requests

from app.builder import SCENE_RULES, detect_industry, match_scenes
from app.taxonomy import FALLBACK_SCENE, INDUSTRIES as _INDUSTRY_TABLE
from app.rag import config as C
from app import netcheck

SCENE_NAMES = [r["name"] for r in SCENE_RULES]
INDUSTRIES = tuple(_INDUSTRY_TABLE)
NUM_RE = re.compile(r"\d+")
FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)
LLM_TIMEOUT = float(os.getenv("AGENT_PARSE_TIMEOUT", "12"))
# 规则引擎已经认出 2 个及以上明确场景时，不再补写 LLM 场景：
# 此时需求已经写清楚，LLM 再补的大概率是臆测，会改变路数分配。
MAX_BASELINE_FOR_NORMALIZE = 1

SYSTEM_PROMPT = (
    "你是安防方案需求结构化助手，只输出一个 JSON 对象，不要解释、不要 Markdown 标题。\n"
    "字段：\n"
    '  "project_name": 项目名字符串（可为 null）\n'
    '  "industry": 行业名，只能从下面行业白名单里选\n'
    '  "scenes": 场景名数组，只能从下面场景白名单里选\n'
    "行业白名单：" + "、".join(INDUSTRIES) + "\n"
    "场景白名单：" + "、".join(SCENE_NAMES) + "\n"
    "严禁输出任何数字（点位数、天数、金额都不归你管）；"
    "严禁输出白名单以外的场景；原文没提到的场景不要臆测。"
)

GAP_SYSTEM_PROMPT = (
    "你是安防售前需求澄清助手。把下面每条「方案缺口」改写成一句面向客户的具体追问。\n"
    '只输出 JSON：{"questions": ["...", "..."]}，数组长度必须与缺口条数完全一致，顺序一一对应。\n'
    "硬性要求：\n"
    "- 只做措辞改写，不得新增任何事实、设备型号、参数或数字\n"
    "- 追问里严禁出现任何阿拉伯数字，也不得给出默认建议值、不得替客户做决定\n"
    "- 每条不超过 60 个汉字，语气简洁、可直接照着回答\n"
    "- 不要复述缺口原文里的数字，不要写「默认」「建议」等字样"
)

_GAP_CACHE: dict[tuple, dict] = {}
_GAP_LOCK = threading.Lock()

# 中文数量词 → 阿拉伯数字：纯字面转换，数字来自原文而非大模型，
# 只匹配「中文数字 + 点位数/天数等量词」，避免误伤「一期」「三合一」等非数量词。
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN_NUM_RE = re.compile(
    r"([零一二三四五六七八九十百千万两]{1,8})\s*"
    r"(?=(?:个点位|台摄像机|个监控|摄像机|点位|路|天))"
)


def _cn_to_int(s: str) -> int | None:
    total = section = number = 0
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                total += (section + number) * unit
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    value = total + section + number
    return value if value > 0 else None


def normalize_cn_numbers(text: str) -> tuple[str, list[str]]:
    """把「一百个点位」「三十天」转成「100个点位」「30天」，返回 (文本, 变更说明)。

    纯本地确定性转换，不调大模型、不推测任何口径（「一个月」不做换算）。
    """
    changed: list[str] = []

    def repl(m: re.Match) -> str:
        value = _cn_to_int(m.group(1))
        if value is None:
            return m.group(0)
        changed.append(f"{m.group(1)}→{value}")
        return str(value)

    return _CN_NUM_RE.sub(repl, text or ""), changed


def _post_chat(system: str, user: str, max_tokens: int = 300) -> dict:
    """统一的 OpenAI 兼容调用；异常一律抛给上层决定降级。"""
    payload = {
        "model": C.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    r = requests.post(
        C.DEEPSEEK_BASE_URL + "/chat/completions",
        headers={"Authorization": "Bearer " + C.DEEPSEEK_API_KEY,
                 "Content-Type": "application/json"},
        json=payload,
        timeout=LLM_TIMEOUT,
    )
    r.raise_for_status()
    content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
    m = FENCE_RE.search(content or "")
    if m:
        content = m.group(1)
    data = json.loads(content.strip())
    return data if isinstance(data, dict) else {}


def _json_from_llm(text: str) -> dict:
    return _post_chat(SYSTEM_PROMPT, "需求原文：\n" + text[:1500])


def _json_for_gaps(gaps: list[str]) -> dict:
    lines = "\n".join(f"{i + 1}. {g}" for i, g in enumerate(gaps))
    return _post_chat(GAP_SYSTEM_PROMPT, "缺口列表：\n" + lines, max_tokens=400)


def _digits_traceable(value: object, original: str) -> bool:
    """LLM 给出的文本里出现的每个数字，都必须在原文里逐字出现。"""
    return all(d in original for d in NUM_RE.findall(str(value or "")))


def parse_requirement(text: str, use_llm: bool = True) -> dict:
    """返回 {text, engine, scenes, project_name, industry, normalized_scenes, ...}。

    `text` 是交给 build_spec 的文本。规则引擎没认清需求时，会把 LLM 归一化
    出来的白名单场景词追加到末尾；追加内容不含任何数字，不会污染正则与公式。
    """
    original = (text or "").strip()
    original, cn_changes = normalize_cn_numbers(original)
    baseline = [r["name"] for r in match_scenes(original)]
    result = {
        "text": original,
        "engine": "rule",
        "scenes": baseline[:],
        "project_name": None,
        "industry": None,
        "industry_suggestion": None,
        "normalized_scenes": [],
        "number_normalized": cn_changes,
        "dropped_scenes": [],
        "warnings": [],
    }
    online = netcheck.llm_reachable(C.DEEPSEEK_BASE_URL)
    if not online:
        result["engine"] = "offline"
        result["warnings"].append("当前无网络，需求解析走本地规则引擎")
        return result
    if not (use_llm and C.DEEPSEEK_API_KEY):
        return result

    try:
        data = _json_from_llm(original)
    except Exception as exc:  # noqa: BLE001 —— 降级是设计的一部分
        result["warnings"].append(f"大模型解析失败，已回退规则引擎：{type(exc).__name__}")
        return result

    # 场景：只收白名单、不含无法回查的数字、去重
    scenes: list[str] = []
    dropped: list[str] = []
    for s in data.get("scenes") or []:
        if not isinstance(s, str) or s not in SCENE_NAMES or not _digits_traceable(s, original):
            dropped.append(str(s))
        elif s not in scenes:
            scenes.append(s)

    # 项目名 / 行业：出现原文没有的数字就整个字段作废
    for key in ("project_name", "industry"):
        value = data.get(key)
        if value and not _digits_traceable(value, original):
            data[key] = None
            dropped.append(key)
            result["warnings"].append(f"已丢弃含未回查数字的 {key}")

    industry = data.get("industry") if data.get("industry") in INDUSTRIES else None
    result.update({
        "engine": "llm",
        "project_name": data.get("project_name") or None,
        "industry": industry,
        "industry_suggestion": industry,
        "dropped_scenes": dropped,
    })
    if dropped:
        result["warnings"].append("已丢弃不合规的 LLM 场景/字段：" + "、".join(dropped[:4]))
    if industry and industry != detect_industry(original)[0]:
        result["warnings"].append(
            f"LLM 建议行业为「{industry}」，仅作提示；实际分类仍按原文关键词判定"
        )

    # 归一化：把规则引擎没认出来的白名单场景补写进原文
    extra = [s for s in scenes if s not in baseline and s != FALLBACK_SCENE]
    if len(baseline) <= MAX_BASELINE_FOR_NORMALIZE and extra:
        result["text"] = original + "\n识别场景：" + "、".join(extra)
        result["normalized_scenes"] = extra
        result["warnings"].append(
            "LLM 补写场景：" + "、".join(extra) +
            "（规则引擎原识别：" + "、".join(baseline) + "）"
        )
    elif len(baseline) > MAX_BASELINE_FOR_NORMALIZE and extra:
        result["warnings"].append(
            "规则引擎已识别 " + str(len(baseline)) + " 个明确场景，未采纳 LLM 补写：" +
            "、".join(extra)
        )
    # 前端展示的是补写后真正生效的场景，而不是 LLM 自报的场景
    result["scenes"] = [r["name"] for r in match_scenes(result["text"])]
    return result


def _local_questions(gaps: list[str]) -> list[dict]:
    """零 Key / 离线时的本地追问模板；question 里一律不含数字。"""
    rules = [
        (("点位数", "路", "点位"), "请给出项目一共需要多少个摄像机点位，并说明各区域分别多少路"),
        (("存储天数", "保存天数", "录像", "天"), "请明确录像需要保存多少天，是否有重点区域要单独延长"),
        (("型号", "白名单", "设备"), "请确认设备是否有指定品牌或指定系列，或确认按产品库推荐选型"),
        (("场景", "区域", "覆盖范围"), "请说明还有哪些区域需要覆盖，以及每个区域的监控目标"),
        (("带宽", "码流"), "请确认是否对画面清晰度或码流档位有特别要求"),
    ]
    out = []
    for g in gaps:
        question = next((q for keys, q in rules if any(k in g for k in keys)), None)
        out.append({"gap": g, "question": question or "请补充这项缺口对应的具体信息，数字请写明确"})
    return out


def phrase_gaps(gaps: list[str], use_llm: bool = True) -> dict:
    """把硬闸门缺口改写成面向客户的具体追问。

    LLM 只负责措辞，不得产任何数字；条数 / 长度 / 数字任一不合规就整批回退
    本地模板。结果按 (缺口, 在线状态, Key 状态) 缓存，避免 LangGraph 在
    resume 重放节点时重复调用大模型。
    """
    clean = [str(g).strip() for g in (gaps or []) if str(g).strip()]
    base = {"items": _local_questions(clean), "engine": "rule", "warnings": []}
    if not clean:
        return base
    online = netcheck.llm_reachable(C.DEEPSEEK_BASE_URL)
    if not online:
        base["engine"] = "offline"
        base["warnings"].append("当前无网络，追问走本地模板")
        return base
    if not (use_llm and C.DEEPSEEK_API_KEY):
        return base

    key = (tuple(clean), C.DEEPSEEK_MODEL, True, True)
    with _GAP_LOCK:
        cached = _GAP_CACHE.get(key)
    if cached:
        return dict(cached)

    try:
        qs = _json_for_gaps(clean).get("questions")
        if not isinstance(qs, list) or len(qs) != len(clean):
            raise ValueError("追问条数与缺口不匹配")
        items = []
        for g, q in zip(clean, qs):
            if (not isinstance(q, str) or not q.strip() or len(q) > 80
                    or NUM_RE.search(q)):
                raise ValueError("追问含数字或格式不合规")
            items.append({"gap": g, "question": q.strip()})
        out = {"items": items, "engine": "llm", "warnings": []}
    except Exception as exc:  # noqa: BLE001 —— 降级是设计的一部分
        base["warnings"].append(f"大模型追问改写失败，已回退本地模板：{type(exc).__name__}")
        out = base

    with _GAP_LOCK:
        _GAP_CACHE[key] = dict(out)
    return out


def clear_gap_cache() -> None:
    """供测试与配置变更后清空追问缓存。"""
    with _GAP_LOCK:
        _GAP_CACHE.clear()


def status() -> dict:
    """给前端展示当前解析引擎。"""
    online = netcheck.llm_reachable(C.DEEPSEEK_BASE_URL)
    if not online:
        return {"engine": "offline", "label": "本地规则解析（离线）", "online": False}
    if C.DEEPSEEK_API_KEY:
        return {"engine": "llm", "label": f"LLM 语义解析（{C.DEEPSEEK_MODEL}）", "online": True}
    return {"engine": "rule", "label": "本地规则解析（未配置 Key）", "online": True}
