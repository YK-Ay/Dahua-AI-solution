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
        require_price: bool = True,
    ) -> dict | None:
        """在指定类目里挑一个最便宜的匹配型号；找不到返回 None。

        require_price=False 时允许返回没有标准价的型号（如拼接单元），
        用于必须给出型号、价格留待商务询价的场景。
        """
        if not class_names:
            return None
        placeholders = ",".join("?" * len(class_names))
        sql = (
            "SELECT model, display_name, price_stand, class_name, year_new FROM products "
            f"WHERE class_name IN ({placeholders}) "
            "AND model IS NOT NULL AND model != '/'"
        )
        if require_price:
            sql += " AND price_stand IS NOT NULL"
        params: list = list(class_names)
        for kw in keywords:
            sql += " AND display_name LIKE ?"
            params.append(f"%{kw}%")
        if model_pattern:
            sql += " AND model LIKE ?"
            params.append(f"%{model_pattern}%")
        sql += " ORDER BY price_stand IS NULL, price_stand ASC, model ASC LIMIT 1"

        row = self.conn.execute(sql, params).fetchone()
        if row is None and model_pattern:
            return self.pick_model(class_names, keywords, None, require_price)
        return dict(row) if row else None

    def pick_from_boms(
        self,
        industry: str | list[str] | None,
        class_names: list[str],
        keywords: list[str],
    ) -> dict | None:
        """从同行业方案配单里挑被引用最多的型号（方案库参与选型）。

        先按行业（可传多个，用于园区这类跨行业场景）精确匹配；没有命中再放宽
        到全部行业。返回的 refs 是引用该型号的方案数，scope 标明是否命中同行业，
        用于在选型依据里如实说明"参考了多少个真实方案配单"。
        """
        if not class_names:
            return None
        industries = [industry] if isinstance(industry, str) else list(industry or [])

        def query(scoped: bool) -> sqlite3.Row | None:
            placeholders = ",".join("?" * len(class_names))
            kw_sql = "".join(" AND p.display_name LIKE ?" for _ in keywords)
            scope_sql = ""
            if scoped:
                scope_sql = " AND b.industry IN (" + ",".join("?" * len(industries)) + ")"
            sql = (
                "SELECT p.model, p.display_name, p.price_stand, p.class_name, "
                "p.year_new, COUNT(DISTINCT bi.scheme_id) AS refs "
                "FROM bom_items bi "
                "JOIN boms b ON b.scheme_id = bi.scheme_id "
                "JOIN products p ON p.model = bi.model "
                f"WHERE p.class_name IN ({placeholders}) "
                "AND p.price_stand IS NOT NULL"
                + kw_sql
                + scope_sql
                + " GROUP BY p.model ORDER BY refs DESC, p.year_new DESC, "
                  "p.price_stand ASC LIMIT 1"
            )
            params: list = list(class_names) + [f"%{k}%" for k in keywords]
            if scoped:
                params += industries
            return self.conn.execute(sql, params).fetchone()

        row = query(True) if industries else None
        scope = "industry"
        if row is None:
            row = query(False)
            scope = "all"
        if row is None:
            return None
        data = dict(row)
        data["refs"] = int(row["refs"] or 0)
        data["scope"] = scope
        return data

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
