"""RAG 参数：全部可用环境变量覆盖，不写死在业务代码里。

设计参考《华为云方案助手融入改造清单》Step 2：
  「alpha/k/阈值放 .env；向量挂了降级关键词，永不空结果」
"""
from __future__ import annotations

import os

# ---- 混合召回 ----
KEYWORD_TOP = int(os.getenv("RAG_KEYWORD_TOP", "40"))     # 关键词路召回条数
VECTOR_TOP = int(os.getenv("RAG_VECTOR_TOP", "15"))       # 语义路召回条数
RRF_ALPHA = float(os.getenv("RAG_RRF_ALPHA", "0.5"))      # 关键词路权重
RRF_K = int(os.getenv("RAG_RRF_K", "60"))                 # RRF 排名平滑常数
# 语义路最小 idf：df 太高的泛词（参数/价格等）没有区分度，直接丢弃；
# 丢到空时语义路返回空，等价于自动降级纯关键词
MIN_SEM_IDF = float(os.getenv("RAG_MIN_SEM_IDF", "1.0"))
# 查询里出现具体型号（DH-xxx / DS-xxx）时，以关键词精确路为主
MODEL_QUERY_ALPHA = float(os.getenv("RAG_MODEL_QUERY_ALPHA", "0.85"))
# 只被单路召回的资料温和降权（另一路整体为空时不启用）
SINGLE_ROUTE_PENALTY = float(os.getenv("RAG_SINGLE_ROUTE_PENALTY", "0.85"))
LOW_RELEVANCE = float(os.getenv("RAG_LOW_RELEVANCE", "0.006"))  # 低于此分提示相关性偏弱
MIN_HITS = int(os.getenv("RAG_MIN_HITS", "1"))            # 至少要有词面重叠
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "8"))

# ---- 检索子集路由（防竞品资料给大华背书）----
SUBSETS = {
    # 出大华主方案：只用大华文档、官方配单、产品库、官网案例
    "solution": ["dahua_solution", "bom", "product", "case"],
    # 竞品分析：只用竞品方案 + 竞品参数事实 + 大华参数事实（客观对比）
    "competitor": ["competitor_solution", "competitor_fact"],
    # 全库：调试用，不用于生成正式方案
    "all": ["dahua_solution", "competitor_solution", "bom", "product", "case", "competitor_fact"],
}
# 产品文档数量远大于方案文档，降权避免刷屏
SUBSET_BOOST = {
    "dahua_solution": 1.25,
    "bom": 1.35,          # 配单是「具体配什么」的硬依据，优先级最高
    "case": 1.0,
    "product": 0.65,
    "competitor_solution": 1.0,
    "competitor_fact": 0.9,
}

# 行业别名：用户口语 → 库里的行业标签（模糊包含匹配）
INDUSTRY_ALIASES = {
    "安防监控": "智慧园区", "园区": "智慧园区", "小区": "小区社区",
    "社区": "小区社区", "住宅": "小区社区", "校园": "智慧校园",
    "学校": "智慧校园", "工厂": "工厂园区", "园区厂区": "工厂园区",
    "交通": "智能交通", "停车": "城市停车", "商铺": "中小商铺",
    "门店": "中小商铺", "商超": "连锁商超", "连锁": "连锁商超",
    "加油站": "加油加气站", "化工": "中小化工厂", "酒店": "酒店民宿",
    "医院": "智慧医院", "景区": "智慧景区", "物流": "智慧物流",
    "金融": "金融", "能源": "电力", "电力": "智慧电力",
}

# ---- 生成层 ----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(os.getenv("RAG_LLM_TIMEOUT", "60"))
CONTEXT_CHARS = int(os.getenv("RAG_CONTEXT_CHARS", "700"))  # 每篇资料进 prompt 的最大字数

DISCLAIMER = "AI 生成内容仅供参考，投标前请人工核对参数与报价；价格以大华云商实际下单为准。"

SOURCE_LABELS = {
    "dahua_solution": "大华方案文档",
    "competitor_solution": "竞品方案文档",
    "bom": "大华云商官方配单",
    "product": "产品库",
    "case": "官网行业案例",
    "competitor_fact": "竞品参数事实",
}
