"""知识库迁移脚本（一次性）。

- 为 solutions 增加 summary 字段，并从 raw_json 的 scheDescribe / content / detail.detail
  提取纯文本作为方案简介（去除 HTML 标签）。
- 为 products 增加 description 字段占位（当前无详情来源，供后续爬取回填）。
可重复运行：列已存在则跳过，summary 仅当为空时回填。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from config.settings import DB_PATH

_TAG = re.compile(r"<[^>]+>")


def stripHtml(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(_TAG, "", str(s)).strip()


def main() -> None:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row

    # 1) solutions.summary
    cols = {r[1] for r in con.execute("PRAGMA table_info(solutions)")}
    if "summary" not in cols:
        con.execute("ALTER TABLE solutions ADD COLUMN summary TEXT")
        print("solutions: 已添加 summary 列")
    n = 0
    for row in con.execute("SELECT sol_id, raw_json, summary FROM solutions"):
        if row["summary"]:
            continue
        summary = ""
        try:
            d = __import__("json").loads(row["raw_json"] or "{}")
            li = d.get("list_item", {})
            summary = stripHtml(li.get("scheDescribe") or li.get("content") or "")
            if not summary:
                det = d.get("detail", {})
                summary = stripHtml(det.get("detail") or "")
        except Exception:
            pass
        if summary:
            con.execute("UPDATE solutions SET summary=? WHERE sol_id=?", (summary[:500], row["sol_id"]))
            n += 1
    con.commit()
    print(f"solutions: 回填简介 {n} 条")

    # 2) products.description（占位，暂无来源）
    pcols = {r[1] for r in con.execute("PRAGMA table_info(products)")}
    if "description" not in pcols:
        con.execute("ALTER TABLE products ADD COLUMN description TEXT")
        print("products: 已添加 description 列（待爬取回填）")

    # 3) 校验
    print("solutions 含简介数:", con.execute(
        "SELECT COUNT(*) FROM solutions WHERE summary IS NOT NULL AND summary<>''").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
