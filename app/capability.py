"""设备能力标签：把产品自由文本归一到可解释的能力维度。

选型链变成「行业专属类目 → 能力标签匹配 → 通用兜底」：
- 类目只能表达「这是什么形态」，表达不了「它会不会测温、防爆、抓拍车牌」；
- 能力标签让「变电设备测温」这类需求在专属类目缺货时还能找到真正的替代型号，
  而不是随手拿一台通用枪机冒充；
- 每个结论都能在 reason 里写清命中了哪些能力，人工复核有据可依。

约束：本模块只做词表匹配与排序，不产出型号、价格、数量，也不调 LLM。
"""
from __future__ import annotations

import sqlite3
import threading

from config.settings import DB_PATH

# 能力标签 → 识别关键词。命中任一关键词即打上该标签。
# 关键词只写产品名/描述里真实出现的词，避免臆造能力。
CAPABILITY_RULES: dict[str, tuple[str, ...]] = {
    "热成像测温": ("测温", "热成像", "双光谱", "红外热", "红外测温"),
    "输电通道": ("输电", "防外破", "杆塔"),
    "防爆": ("防爆",),
    "车牌抓拍": ("车牌", "卡口", "抓拍单元", "电子警察", "电警"),
    "人脸识别": ("人脸",),
    "周界人车": ("周界", "警戒", "人车", "绊线"),
    "电瓶车入梯": ("入梯", "电瓶车"),
    "全景鱼眼": ("全景", "鱼眼", "拼接"),
    "雷达探测": ("雷达", "毫米波"),
    "音频采集": ("音频", "拾音"),
    "烟火检测": ("烟火", "火焰", "火点"),
    "变倍云台": ("变倍", "云台", "球机"),
    "星光全彩": ("星光", "全彩"),
    "气体探测": ("气体探测", "可燃气体", "有毒气体"),
}

# 摄像机类目：能力兜底时优先选这些类目，避免把 NVR/硬盘当成摄像机
CAMERA_LIKE_CLASSES = (
    "网络摄像机", "分销球机", "球机", "项目型网络摄像机",
    "全景系列网络摄像机", "行业球机产品", "行业夜视王",
    "测温型枪型摄像机", "测温型双光谱球型摄像机", "观测型双光谱球型摄像机",
    "观测型双光谱重型云台", "输电相机", "防爆摄像机", "防爆多功能摄像机",
    "防爆防腐蚀产品", "水利相机", "萤光系列", "车行出入口", "人行出入口",
)

_index_lock = threading.Lock()
_index_cache: dict[tuple[str, float], dict[str, frozenset[str]]] = {}


def tags_of_text(text: str) -> frozenset[str]:
    """从一段文本里抽能力标签（纯函数，便于单测）。"""
    text = text or ""
    return frozenset(
        tag for tag, words in CAPABILITY_RULES.items()
        if any(w in text for w in words)
    )


def tags_for(product: dict) -> frozenset[str]:
    """从一个产品记录里抽能力标签（display_name + class_name + prod_desc）。"""
    text = " ".join(str(product.get(k) or "") for k in
                    ("display_name", "class_name", "prod_desc", "description", "spec_value"))
    return tags_of_text(text)


def _db_key(conn: sqlite3.Connection) -> tuple[str, float]:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = (row[2] if row else "") or ""
    try:
        import os
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return path, mtime


def build_index(conn: sqlite3.Connection) -> dict[str, frozenset[str]]:
    """一次性给全库产品打标，进程内复用；库文件变化时按 mtime 自动重建。"""
    index: dict[str, frozenset[str]] = {}
    for row in conn.execute(
        "SELECT model, display_name, class_name, "
        "COALESCE(prod_desc, '') AS prod_desc FROM products "
        "WHERE model IS NOT NULL AND model != '/'"
    ):
        index[row["model"]] = tags_of_text(
            " ".join(str(row[k] or "") for k in ("display_name", "class_name", "prod_desc"))
        )
    return index


def get_index(repo) -> dict[str, frozenset[str]]:
    key = _db_key(repo.conn)
    with _index_lock:
        index = _index_cache.get(key)
        if index is None:
            index = build_index(repo.conn)
            _index_cache.clear()
            _index_cache[key] = index
        return index


def tags_of(repo, model: str) -> frozenset[str]:
    return get_index(repo).get(model, frozenset())


def rank_candidates(
    repo,
    classes: list[str],
    required: list[str],
    require_price: bool = True,
    limit: int = 5,
) -> list[dict]:
    """按能力重合度给候选型号排序。

    classes 为空表示跨类目搜索；有值则先限定类目。
    require_price=False 时允许返回无标准价的型号（价格留待询价）。
    返回 [{model, display_name, class_name, price_stand, year_new,
           matched: [命中的能力], capabilities: [全部能力]}]，按重合度降序。
    """
    required = [t for t in required if t in CAPABILITY_RULES]
    if not required:
        return []
    index = get_index(repo)
    sql = (
        "SELECT model, display_name, class_name, price_stand, year_new FROM products "
        "WHERE model IS NOT NULL AND model != '/'"
    )
    params: list = []
    if classes:
        sql += " AND class_name IN (" + ",".join("?" * len(classes)) + ")"
        params.extend(classes)
    if require_price:
        sql += " AND price_stand IS NOT NULL"
    out: list[dict] = []
    for row in repo.conn.execute(sql, params):
        caps = index.get(row["model"]) or frozenset()
        matched = [t for t in required if t in caps]
        if not matched:
            continue
        out.append({
            "model": row["model"],
            "display_name": row["display_name"],
            "class_name": row["class_name"],
            "price_stand": row["price_stand"],
            "year_new": row["year_new"],
            "matched": matched,
            "capabilities": sorted(caps),
            "score": len(matched),
        })
    out.sort(key=lambda c: (
        -c["score"],
        c["class_name"] not in CAMERA_LIKE_CLASSES,
        c["price_stand"] is None,
        c["price_stand"] or 0,
        c["model"],
    ))
    return out[:max(1, limit)]


def closest(repo, required: list[str], limit: int = 3) -> list[dict]:
    """库内能力最接近的替代型号，只用于报缺提示，绝不自动替换。"""
    return rank_candidates(repo, [], required, require_price=False, limit=limit)


def describe(product: dict) -> str:
    """把候选型号整理成「型号（能力）」的一行说明。"""
    caps = "、".join(product.get("matched") or product.get("capabilities") or [])
    return f"{product['model']}（{caps}）" if caps else product["model"]
