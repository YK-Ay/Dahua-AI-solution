"""三方向代表型号样本（正常销售、按价格排）。"""
import sqlite3
import sys

from config.settings import DB_PATH

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA journal_mode=MEMORY")

for direction, cats in [
    ("园区楼宇", ["网络摄像机", "分销球机", "通用存储录像机", "监控专用PoE交换机", "人行出入口"]),
    ("交管", ["车行出入口", "抓拍摄像机"]),
]:
    print(f"\n========== {direction} ==========")
    for cat in cats:
        rows = conn.execute(
            """SELECT model, display_name, price_stand FROM products
               WHERE direction=? AND class_name=? AND pi_status='正常销售'
               AND price_stand IS NOT NULL AND price_stand>0
               ORDER BY price_stand DESC LIMIT 4""",
            (direction, cat)).fetchall()
        if rows:
            print(f"\n[{cat}]")
            for r in rows:
                print(f"  {r[0]:<26} {int(r[2]):>6}元  {r[1][:30]}")

conn.close()
