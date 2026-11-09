"""初始化知识库：执行 schema.sql。"""
import sqlite3
from pathlib import Path

from config.settings import DB_PATH

schema = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")
conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA journal_mode=MEMORY")
conn.executescript(schema)
conn.commit()
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("DB 初始化完成:", DB_PATH)
print("表:", tables)
conn.close()
