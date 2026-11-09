"""RAG 层：混合检索 + 来源标注反幻觉。

文件分工（从下往上读）：
  config.py     参数与子集/行业路由规则
  tokenizer.py  零依赖中文分词（型号精确 token + 中文 bigram）
  sources.py    把 SQLite 里 5 类数据统一成「资料文档」
  index.py      BM25 + 本地语义近似 → RRF 融合 → 失败降级
  prompt.py     反幻觉硬约束提示词与 [资料N] 引用块
  generator.py  LLM（可选）/ 抽取式降级生成 + 后置数字型号校验
  service.py    对外唯一入口 ask()

对外只用 service.ask() 与 service.status()，其他文件都是内部实现。
"""
from .service import ask, reindex, status  # noqa: F401

__all__ = ["ask", "reindex", "status"]
