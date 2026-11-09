"""方案知识库入库：清洗(仅海康) + 切分 + 写入 SQLite，供后续 RAG 检索。

用法：
  python scraper/ingest_kb.py [--kb-root PATH] [--db PATH] [--reset]

- 大华股份/105 篇：已干净，原样入库
- 海康威视/41 篇：剔除首尾导航菜单噪声后再入库
- 每篇按 1000 字 / 200 字重叠 切分，写入 kb_docs + kb_chunks
- metadata（vendor/industry/source）随块落库，方便以后按"大华子集 / 竞品子集"检索
"""
import argparse
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from config.settings import DB_PATH  # noqa: E402

KB_ROOT_DEFAULT = BASE_DIR / "data" / "方案知识库"  # 默认放在项目 data/ 下，也可用 --kb-root 指定
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
SEP = "=" * 20

META_KEYS = {"厂商": "vendor", "标题": "title", "行业标签": "industry", "来源": "source", "摘要": "summary"}


def parse_header(text):
    meta = {v: "" for v in META_KEYS.values()}
    head = text.split(SEP)[0] if SEP in text else ""
    for line in head.splitlines():
        m = re.match(r"【(厂商|标题|行业标签|来源|摘要)】(.*)", line)
        if m:
            meta[META_KEYS[m.group(1)]] = m.group(2).strip()
    return meta


def is_real_line(s):
    s = s.strip()
    return len(s) >= 12 or bool(re.search(r"[，。：；！？、]", s))


def clean_body(body, vendor):
    """海康 txt 首尾混了导航菜单，找到首/尾真实内容行，截掉中间噪声。大华原样保留。"""
    if vendor != "海康威视":
        return body.strip()
    lines = body.split("\n")
    start = 0
    while start < len(lines) and not is_real_line(lines[start]):
        start += 1
    end = len(lines) - 1
    while end > start and not is_real_line(lines[end]):
        end -= 1
    return "\n".join(lines[start : end + 1]).strip()


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    text = text.strip()
    if not text:
        return []
    step = max(1, size - overlap)
    return [text[i : i + size] for i in range(0, len(text), step) if text[i : i + size].strip()]


def ingest(kb_root, db_path, reset):
    import sqlite3

    con = sqlite3.connect(db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS kb_docs (
            doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT, subset TEXT, title TEXT, industry TEXT,
            source TEXT, summary TEXT, char_count INTEGER,
            chunk_count INTEGER, raw_path TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS kb_chunks (
            chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER, seq INTEGER, text TEXT, char_count INTEGER,
            FOREIGN KEY (doc_id) REFERENCES kb_docs(doc_id))"""
    )
    if reset:
        con.execute("DELETE FROM kb_chunks")
        con.execute("DELETE FROM kb_docs")

    files = []
    for sub, vendor, subset in [
        ("大华股份", "大华股份", "dahua_solution"),
        ("海康威视", "海康威视", "competitor_solution"),
    ]:
        d = kb_root / sub
        if d.exists():
            for f in sorted(d.glob("*.txt")):
                files.append((f, vendor, subset))

    total_docs = total_chunks = 0
    for f, vendor, subset in files:
        text = f.read_text(encoding="utf-8")
        meta = parse_header(text)
        if not meta["vendor"]:
            meta["vendor"] = vendor
        body = text.split(SEP)[1] if SEP in text else text
        clean = clean_body(body, vendor)
        chunks = chunk_text(clean)
        cur = con.execute(
            "INSERT INTO kb_docs (vendor,subset,title,industry,source,summary,char_count,chunk_count,raw_path) VALUES (?,?,?,?,?,?,?,?,?)",
            (meta["vendor"], subset, meta["title"], meta["industry"], meta["source"],
             meta["summary"], len(clean), len(chunks), str(f)),
        )
        doc_id = cur.lastrowid
        con.executemany(
            "INSERT INTO kb_chunks (doc_id,seq,text,char_count) VALUES (?,?,?,?)",
            [(doc_id, i + 1, c, len(c)) for i, c in enumerate(chunks)],
        )
        total_docs += 1
        total_chunks += len(chunks)

    con.commit()
    print(f"入库完成：文档 {total_docs} 篇，分块 {total_chunks} 块。")
    print("按子集统计：")
    for row in con.execute("SELECT subset, COUNT(*) FROM kb_docs GROUP BY subset"):
        print(f"  {row[0]}: {row[1]} 篇")
    print("样例(前 3)：")
    for row in con.execute("SELECT vendor, title, industry FROM kb_docs LIMIT 3"):
        print(f"  [{row[0]}] {row[1]} | {row[2]}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-root", default=str(KB_ROOT_DEFAULT))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    ingest(Path(a.kb_root), Path(a.db), a.reset)
