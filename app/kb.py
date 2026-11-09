"""知识库读取：产品白名单与按场景选型。

只查本地 SQLite（W2 采集自大华云商公开产品），不联网。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config.settings import DB_PATH


class ProductRepo:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def __enter__(self) -> "ProductRepo":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def allowed_models(self) -> set[str]:
        """产品白名单：库里真实存在的订货型号。"""
        rows = self.conn.execute(
            "SELECT DISTINCT model FROM products "
            "WHERE model IS NOT NULL AND model != '/' AND model != ''"
        ).fetchall()
        return {r["model"] for r in rows}

    def pick_model(
        self,
        class_names: list[str],
        keywords: list[str],
        model_pattern: str | None = None,
    ) -> dict | None:
        """在指定类目里挑一个最便宜的匹配型号；找不到返回 None。"""
        if not class_names:
            return None
        placeholders = ",".join("?" * len(class_names))
        sql = (
            "SELECT model, display_name, price_stand, class_name FROM products "
            f"WHERE class_name IN ({placeholders}) "
            "AND price_stand IS NOT NULL AND model IS NOT NULL AND model != '/'"
        )
        params: list = list(class_names)
        for kw in keywords:
            sql += " AND display_name LIKE ?"
            params.append(f"%{kw}%")
        if model_pattern:
            sql += " AND model LIKE ?"
            params.append(f"%{model_pattern}%")
        sql += " ORDER BY price_stand ASC LIMIT 1"

        row = self.conn.execute(sql, params).fetchone()
        if row is None and model_pattern:
            return self.pick_model(class_names, keywords, None)
        return dict(row) if row else None

    def search(
        self,
        keyword: str = "",
        class_name: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """按关键词模糊检索产品（型号或名称），供 MCP 的 search_kb 工具使用。"""
        sql = (
            "SELECT model, display_name, price_stand, class_name FROM products "
            "WHERE model IS NOT NULL AND model != '/'"
        )
        params: list = []
        if keyword:
            sql += " AND (model LIKE ? OR display_name LIKE ?)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        if class_name:
            sql += " AND class_name LIKE ?"
            params.append(f"%{class_name}%")
        sql += " ORDER BY price_stand IS NULL, price_stand ASC LIMIT ?"
        params.append(max(1, min(limit, 50)))
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def info(self, model: str) -> dict | None:
        row = self.conn.execute(
            "SELECT model, display_name, price_stand, class_name FROM products WHERE model = ? LIMIT 1",
            (model,),
        ).fetchone()
        return dict(row) if row else None
