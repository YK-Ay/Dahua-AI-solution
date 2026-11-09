# -*- coding: utf-8 -*-
"""查看知识库当前状态"""
import sqlite3
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

DB = r'db\dahua_kb.sqlite'
con = sqlite3.connect(DB)
cur = con.cursor()

print('=== 表清单 ===')
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(r[0])
print()

for t in ['pages', 'products', 'solutions', 'solution_items']:
    try:
        n = cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'{t}: {n} 条')
    except Exception as e:
        print(f'{t}: 不存在 ({e})')
print()

print('=== pages 行业分布 ===')
for r in cur.execute('SELECT industry, page_kind, COUNT(*) FROM pages GROUP BY industry, page_kind ORDER BY industry'):
    print(r)
print()

print('=== products 方向分布 ===')
for r in cur.execute('SELECT direction, COUNT(*) FROM products GROUP BY direction'):
    print(r)
print()

print('=== solutions ===')
for r in cur.execute('SELECT sol_id, name, scene_name, view_count FROM solutions'):
    print(r)
print()

print('=== solution_items 分组统计 ===')
for r in cur.execute('SELECT grp_no, grp_name, COUNT(*) FROM solution_items GROUP BY grp_no ORDER BY grp_no'):
    print(r)
con.close()
