"""W1 验收：统计 pages 表分布 + 抽查两页正文质量。"""
import sqlite3
import sys

from config.settings import DB_PATH

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=MEMORY")
    return conn


conn = get_conn()

print("=== 总览 ===")
total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
print(f"总页数: {total}")

print("\n=== 按行业 × 页面类型 ===")
for row in conn.execute(
    """SELECT industry, page_kind, COUNT(*), ROUND(AVG(content_len))
       FROM pages GROUP BY industry, page_kind ORDER BY industry, page_kind"""
):
    print(f"  {row[0]:<6} {row[1]:<15} {row[2]:>2} 页   平均字数 {int(row[3])}")

print("\n=== tech_page 清单（真技术内容） ===")
for row in conn.execute(
    """SELECT title, tech_hits, content_len FROM pages
       WHERE page_kind='tech_page' ORDER BY tech_hits DESC"""
):
    print(f"  tech={row[1]:>2}  {row[0]}  ({row[2]}字)")

print("\n=== 抽查 1：违停抓拍（tech_page）正文前 200 字 ===")
t = conn.execute("SELECT content_text FROM pages WHERE title LIKE '%违停%'").fetchone()
print(t[0][:200] if t else "未找到")

print("\n=== 抽查 2：智慧园区（marketing_page）正文前 200 字 ===")
t = conn.execute("SELECT content_text FROM pages WHERE title LIKE '%智慧园区解决方案%'").fetchone()
print(t[0][:200] if t else "未找到")

conn.close()
