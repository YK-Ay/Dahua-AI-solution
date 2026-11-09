"""离线自检：断网环境里一键确认本地能力是否完好。

不联网、不启动服务，直接检查数据库、本地图片、静态资源，
并强制以离线模式跑一次 RAG 问答与一次竞品配对，验证降级链路。

用法（在能装依赖的 Python 3.14 下）：
    python scripts/offline_check.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

# 必须在导入 app.* 之前设置，netcheck 会据此跳过所有网络探测
os.environ["APP_OFFLINE"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.settings import BASE_DIR, DB_PATH  # noqa: E402


def line(ok: bool, label: str, detail: str = "") -> bool:
    print(("  [OK]  " if ok else "  [FAIL]") + f" {label}" + (f" · {detail}" if detail else ""))
    return ok


def main() -> int:
    results = []
    print("=== 离线自检（APP_OFFLINE=1，不发起任何网络请求）===\n")

    db = Path(DB_PATH)
    results.append(line(db.is_file(), "SQLite 单文件存在", f"{db.stat().st_size / 1024 / 1024:.1f} MB"))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    expected = {
        "products": 3000, "solutions": 50, "boms": 50, "bom_items": 1000,
        "pages": 20, "kb_docs": 100, "kb_chunks": 200,
        "competitor_facts": 4000, "competitor_products": 100,
    }
    for table, floor in expected.items():
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            results.append(line(n >= floor, f"{table}", f"{n} 条"))
        except sqlite3.OperationalError as exc:
            results.append(line(False, f"{table}", str(exc)))
    cols = {r[1] for r in con.execute("PRAGMA table_info(products)")}
    if "img_local" in cols:
        n = con.execute("SELECT COUNT(*) FROM products WHERE img_local IS NOT NULL AND img_local<>''").fetchone()[0]
        results.append(line(n > 3000, "产品图已本地化", f"{n}/3811"))
    else:
        results.append(line(False, "产品图已本地化", "缺少 img_local 列，请跑 scraper.localize_images"))
    con.close()

    img_dir = BASE_DIR / "web" / "static" / "img"
    n_webp = len(list(img_dir.rglob("*.webp"))) if img_dir.exists() else 0
    # 多个产品可能共用同一张图，文件数会少于产品数
    results.append(line(n_webp > 2500, "本地图片文件", f"{n_webp} 个 webp"))

    static = BASE_DIR / "web" / "static"
    need = ["kb.html", "rag.html", "compare.html", "agent.html", "solution.html",
            "styles.css", "kb.js", "rag.js", "compare.js",
            "agent.js", "solution.js", "topology.js"]
    miss = [f for f in need if not (static / f).is_file()]
    results.append(line(not miss, "前端静态资源",
                        "缺：" + ",".join(miss) if miss else f"{len(need)} 个文件齐全"))

    print("\n--- 本地能力实测 ---")
    t0 = time.time()
    from app.rag import service as rag
    ans = rag.ask("小区监控方案需要哪些摄像机", mode="solution", top_k=4)
    dt = time.time() - t0
    results.append(line(ans.get("mode") == "extractive" and bool(ans.get("answer")),
                        "RAG 离线降级", f"mode={ans.get('mode')}, {dt:.1f}s"))
    results.append(line(dt < 15, "离线问答未卡超时", f"{dt:.1f}s"))
    st = rag.status()
    results.append(line(st.get("offline") is True, "状态接口标记离线", st.get("mode", "")))

    from app import compare_service as cs
    cats = cs.categories()["items"]
    results.append(line(len(cats) == 3 and all(c["hik_models"] > 10 for c in cats),
                        "竞品对比类目与海康候选",
                        ", ".join(f"{c['name']}:{c['hik_models']}" for c in cats)))
    r = cs.pair("网络摄像机", "DH-IPC-HFW5443M1-YL-PV-AS", "DS-2CD1245(D)-LA")
    results.append(line(len(r["rows"]) == 8, "代表款逐字段对比", f"{len(r['rows'])} 个字段"))

    print("\n--- Agent 离线链路 ---")
    from agent import service as agent_service
    from agent.parser import status as agent_status
    results.append(line(agent_status()["engine"] == "offline", "Agent 解析引擎离线降级"))
    a = agent_service.start("某智慧园区：周界20路，出入口10路，存储30天。")
    results.append(line(a["status"] == "awaiting_input" and a["interrupt_type"] == "review",
                        "Agent 审阅中断", f"thread={a['thread_id']}"))
    fin = agent_service.resume(a["thread_id"], {"decision": "approve"})
    results.append(line(fin["status"] == "done" and "docx" in fin["files"],
                        "Agent 离线导出拓扑与 Word"))

    print("\n=== 结果 ===")
    ok = all(results)
    print(("全部通过：本机可完全离线使用。" if ok else "有项目未通过，请检查上面 [FAIL] 项。"))
    print("离线可用：Agent 工作台 / 知识库 / 智能问答(本地检索摘要) / 竞品对比 / 拓扑 PNG / Word 导出")
    print("离线不可用：一键同步、在线大模型生成、外链到官网的来源页")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
