"""反幻觉提示词 + [资料N] 引用块构造。

对应《融入改造清单》Step 1：检索片段统一编号，system prompt 加硬约束，
每个数字/型号/案例都要能回溯到某一篇资料；找不到就明说，不许编。
"""
from __future__ import annotations

from . import config as C
from .index import Hit

# 配单资料较长，单独放宽；其余资料控制长度，避免撑爆上下文
CHARS_BY_SUBSET = {"bom": 2600, "competitor_fact": 1200}


SYSTEM_PROMPT = """你是大华股份资深售前解决方案工程师，只允许依据【检索资料】回答，不得使用资料之外的知识补齐。

【硬性规则 · 违反任何一条都算答错】
1. 每一个数字、型号、价格、产品名、案例名，都必须在句末标注来源，格式严格为 [资料N]，N 是资料编号。
2. 型号必须原样出现在检索资料里，禁止改写、补全或凭印象编造。资料里没有的型号写「需进一步核实」。
3. 价格只能引用资料里的数值，并注明口径（云商分销价 / 标准价 / 指导价）。资料里价格是「原文未提供」时，写「报价需进一步核实」，禁止估算。
4. 【官方配单】资料里同一分类下的多个型号是「可替换候选」，实际选型只能择一；严禁把全部候选累加成采购数量或总价。如需给出总额，必须说明是「按选定型号另行核算」。
5. 禁止用竞品（海康等）资料给大华的产品能力背书。竞品资料只能用于客观对比。
6. 资料没有覆盖的内容，直接写「资料未覆盖，需进一步核实」，不要推测、不要用行业常识填充。
7. 若资料中出现「AI 草稿 / draft」标记，必须提醒用户该部分需人工核对。

【输出结构】用中文，分四节：
一、结论摘要
二、配置建议（表格：设备 / 型号 / 数量 / 单价与口径 / 来源），每行都带 [资料N]
三、依据与出处（列出用到的资料编号、来源、行业）
四、待核实项（明确写出资料没覆盖的部分）

【免责】结尾固定附一句：「AI 生成内容仅供参考，投标前请人工核对参数与报价。」"""


def build_context(hits: list[Hit]) -> str:
    """把命中的资料编号成 [资料1] [资料2] …，供 prompt 与前端共用。"""
    blocks = []
    for i, h in enumerate(hits, 1):
        h.n = i                 # 幂等：服务层已编号，这里保证单独调用时也正确
        d = h.doc
        limit = CHARS_BY_SUBSET.get(d.subset, C.CONTEXT_CHARS)
        text = d.text if len(d.text) <= limit else d.text[:limit] + "\n…（本资料较长，已截断；完整原文见来源）"
        blocks.append(
            f"[资料{i} | 来源:{d.source or d.label} | 行业:{d.industry or '未标注'} | "
            f"类型:{d.label} | 标题:{d.title}]\n{text}"
        )
    return "\n\n" + ("\n\n" + "-" * 60 + "\n\n").join(blocks)


def build_user_prompt(query: str, hits: list[Hit], mode: str) -> str:
    mode_note = {
        "solution": "本次为【大华主方案】生成：只用大华方案文档、官方配单、产品库与官网案例。",
        "competitor": "本次为【竞品客观对比】生成：只用竞品资料与双方官网参数事实，不贬低对手、不替大华背书。",
        "all": "本次为【全库检索调试】，资料可能混有竞品内容，引用时务必区分厂商。",
    }.get(mode, "")
    return (
        f"【检索模式】{mode_note}\n"
        f"【用户需求】{query}\n"
        f"【检索资料】共 {len(hits)} 篇，编号如下：\n"
        f"{build_context(hits)}\n\n"
        "请严格按 system 的硬性规则输出。再次自检：每个型号、每个价格是否都能在上方资料里逐字找到？"
    )


def source_cards(hits: list[Hit]) -> list[dict]:
    """给前端的引用卡片：可点击、可回溯。"""
    return [{
        "n": h.n,
        "title": h.doc.title,
        "source": h.doc.source or h.doc.label,
        "subset": h.doc.subset,
        "label": h.doc.label,
        "industry": h.doc.industry,
        "vendor": h.doc.vendor,
        "url": h.doc.source_url,
        "doc_id": h.doc.doc_id,
        "score": h.score,
        "kw_rank": h.kw_rank,
        "vec_rank": h.vec_rank,
        "snippet": h.snippet,
        "meta": h.doc.meta,
    } for h in hits]
