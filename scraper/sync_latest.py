"""知识库一键同步（真实增量更新，供 /api/kb/sync 调用）。

原同步链 scraper.mall_collect + scraper.collect_solutions_api 的问题：
  1. mall_collect 走 cloudChoiceProd（匿名池 2985 条），比 prodInfoSearch 完整池少；
  2. collect_solutions_api 只抓 4 个 catname（17 条），云商实际有 53 个方案；
  3. 两个脚本都不检查退出码，抓取失败前端仍显示"同步完成"；
  4. 重跑会覆盖已有配单明细，破坏 align_bom_products.py 建立的产品关联。

本脚本：
  - 用匿名游客登录（站点自带，不使用任何用户账号 / 凭证）；
  - 产品：prodInfoSearch 全量池，只新增 + 刷新价格/状态，绝不覆盖已补全的简介/参数/来源；
  - 方案：scheme/list 全量 53 个，只为**新方案**抓配单并落库，已有配单保持不动；
  - solutions 表按 boms 幂等补齐，让知识库方案数与实际一致；
  - 每一步输出真实计数，最后打印 `SYNC_RESULT {...}`；任何关键步骤失败返回非 0。

用法：python -m scraper.sync_latest [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from config.settings import DB_PATH

from scraper.collect_public_products import (
    MALL, make_session, norm_mall, post_json, pull_search,
)
from scraper.import_bom import build_plan, write_db
from scraper.merge_products import classify

SCHEME_LIST = "/smb/api/scheme/list"
CLOUD_PRODS = "/product/api/lectotype/products/cloudProds"


def log(*args):
    print(*args, flush=True)


def backup(db: Path) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = db.with_name(db.name + ".bak-sync-" + stamp)
    shutil.copy2(db, dst)
    return str(dst)


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def sync_products(sess, con: sqlite3.Connection, now: str, dry: bool) -> dict:
    log("▶ [1/4] 拉取大华云商公开产品池（匿名 prodInfoSearch）…")
    raw = pull_search(sess, "  ", "公开产品池")
    if not raw:
        raise RuntimeError("产品池返回为空，疑似接口失效或登录态未就绪")
    cands = [norm_mall(x, "mall_public_search", now) for x in raw]
    log(f"  接口返回 {len(raw)} 条，规范化 {len(cands)} 条")

    existing_ids = {r[0] for r in con.execute("SELECT prod_id FROM products")}
    existing_models = {r[0] for r in con.execute(
        "SELECT model FROM products WHERE model IS NOT NULL AND model<>''")}
    inserts, refreshes, skipped = [], [], 0
    seen = set()
    for c in cands:
        pid, model = c.get("prod_id"), c.get("model")
        if not pid or not model:
            skipped += 1
            continue
        if pid in existing_ids:
            refreshes.append((num(c.get("price_stand")), c.get("pi_status"), now, pid))
            continue
        if model in existing_models or model in seen or pid in seen:
            skipped += 1
            continue
        seen.add(model)
        seen.add(pid)
        year = None
        if c.get("pi_sysj"):
            try:
                year = int(str(c["pi_sysj"])[:4])
            except ValueError:
                pass
        inserts.append((
            pid, model, c.get("display_name"), num(c.get("price_stand")),
            c.get("pi_sysj"), year, c.get("pi_status"), c.get("class_name"),
            json.dumps(c.get("catalog_ids") or [], ensure_ascii=False),
            c.get("img_url"), c.get("part_num"), classify(c.get("class_name")),
            now, c.get("source"), c.get("prod_desc"), c.get("source_url"),
            c.get("spec_value"),
        ))

    log(f"  计划：新增 {len(inserts)} 条 | 刷新 {len(refreshes)} 条 | 跳过重复/无型号 {skipped} 条")
    for r in inserts[:5]:
        log(f"    + {r[1][:34]:34} | {str(r[2])[:30]:30} | ¥{r[3]}")
    if dry:
        return {"fetched": len(raw), "added": len(inserts), "refreshed": len(refreshes), "dry_run": True}

    if inserts:
        con.executemany(
            """INSERT OR IGNORE INTO products
               (prod_id, model, display_name, price_stand, pi_sysj, year_new, pi_status,
                class_name, catalog_ids, img_url, part_num, direction, collected_at,
                source, prod_desc, source_url, spec_value)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", inserts)
    # 只刷新价格（有正数才写）与状态/时间；简介、参数、来源一律不动
    if refreshes:
        con.executemany(
            """UPDATE products SET
                 price_stand = CASE WHEN ? IS NOT NULL AND ? > 0 THEN ? ELSE price_stand END,
                 pi_status   = COALESCE(?, pi_status),
                 collected_at = ?
               WHERE prod_id=?""",
            [(p, p, p, st, ts, pid) for p, st, ts, pid in refreshes],
        )
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    log(f"  ✔ 产品库现有 {total} 条")
    return {"fetched": len(raw), "added": len(inserts), "refreshed": len(refreshes), "total": total}


def backfill_solutions(con: sqlite3.Connection, now: str, dry: bool) -> dict:
    """把 boms 里的方案幂等补进 solutions，解决"云商爬了 53 条、页面只显示 17 条"。"""
    log("▶ [2/4] 对齐 solutions 与云商方案清单…")
    rows = con.execute(
        "SELECT b.scheme_id, b.scheme_name, b.industry, b.view_count, b.collected_at, b.raw_json, "
        "(SELECT COUNT(*) FROM bom_items i WHERE i.scheme_id=b.scheme_id) AS item_count "
        "FROM boms b"
    ).fetchall()
    existing = {r[0] for r in con.execute("SELECT sol_id FROM solutions")}
    pending = [r for r in rows if r[0] not in existing]
    log(f"  云商方案 {len(rows)} 条 | solutions 已有 {len(existing)} 条 | 待补 {len(pending)} 条")
    if dry or not pending:
        return {"boms": len(rows), "added": len(pending), "total": len(rows)}

    payload = []
    for sid, name, industry, view, collected, raw_json, item_n in pending:
        payload.append((
            sid, name, "", industry or "", "", "", view or 0,
            raw_json or "{}", collected or now,
            f"{industry or '通用场景'} · 大华云商官方配单方案，含 {item_n} 条选型产品；"
            "同一分类下多型号为可替换候选，不是累加采购清单。",
        ))
    con.executemany(
        """INSERT OR IGNORE INTO solutions
           (sol_id, name, scene_id, scene_name, detail_img, release_time, view_count,
            raw_json, collected_at, summary)
           VALUES (?,?,?,?,?,?,?,?,?,?)""", payload)
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM solutions").fetchone()[0]
    log(f"  ✔ solutions 现有 {total} 条")
    return {"boms": len(rows), "added": len(pending), "total": total}


def fetch_schemes(sess) -> list[dict]:
    out, page = [], 1
    while page <= 20:
        j = post_json(sess, SCHEME_LIST, {"page": page, "pageSize": 50})
        d = j.get("data") or {}
        lst = d.get("list") or []
        out.extend(lst)
        log(f"  scheme/list page {page}: +{len(lst)} (累计 {len(out)}/{d.get('total')})")
        if len(lst) < 50:
            break
        page += 1
    return out


def sync_schemes(sess, con: sqlite3.Connection, dry: bool) -> dict:
    """方案清单增量更新；只对全新方案抓配单，绝不重写已有配单明细。"""
    log("▶ [3/4] 拉取云商方案清单（scheme/list）…")
    schemes = fetch_schemes(sess)
    if not schemes:
        raise RuntimeError("方案清单返回为空，接口可能失效")
    known = {r[0] for r in con.execute("SELECT scheme_id FROM boms")}
    fresh = [s for s in schemes if s.get("id") and s["id"] not in known]
    log(f"  线上方案 {len(schemes)} 个 | 本地已有 {len(known)} 个 | 新增 {len(fresh)} 个")

    # 接口连通性自检：抽 1 个已有方案验证配单接口确实可取到数据
    probe_id = next(iter(known), None)
    probe = {"ok": False, "items": 0}
    if probe_id:
        try:
            j = post_json(sess, CLOUD_PRODS, {"lectotypeId": probe_id})
            data = j.get("data") or []
            probe = {"ok": j.get("code") == 200 and bool(data), "items": len(data),
                     "code": j.get("code")}
            log(f"  配单接口自检：code={j.get('code')} 分组 {len(data)} 个 → {'正常' if probe['ok'] else '异常'}")
        except Exception as exc:  # noqa: BLE001
            log(f"  配单接口自检失败：{exc}")
    if dry or not fresh:
        return {"online": len(schemes), "new": len(fresh), "probe": probe,
                "added_items": 0, "dry_run": dry}

    # 已有配单保持不动；先提交自己的事务，再让 import_bom.write_db 用独立连接写新方案
    con.commit()
    today = date.today().isoformat()
    added_items = 0
    idx_map, rows = {}, []
    for s in fresh:
        sid = s["id"]
        try:
            detail = post_json(sess, CLOUD_PRODS, {"lectotypeId": sid})
        except Exception as exc:  # noqa: BLE001
            log(f"  ! {sid} 配单抓取失败：{exc}")
            continue
        idx_map[sid] = {"name": s.get("name"), "cat": s.get("catname"),
                        "view": s.get("viewCount") or 0}
        rows.append({"scheme_id": sid, "scheme_name": s.get("name"),
                     "industry": s.get("catname"), "view_count": s.get("viewCount") or 0,
                     "detail": detail, "bom_candidates": [], "fetched_at": today,
                     "_done": True})
    if rows:
        plan = build_plan(rows, idx_map)
        write_db(plan, rows, str(DB_PATH))
        added_items = sum(p["item_count"] for p in plan)
        log(f"  ✔ 新方案 {len(plan)} 个，写入配单明细 {added_items} 条")
    return {"online": len(schemes), "new": len(fresh), "probe": probe,
            "added_items": added_items}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="知识库真实增量同步")
    ap.add_argument("--dry-run", action="store_true", help="只探测接口与计划，不写库")
    args = ap.parse_args()

    db = Path(DB_PATH)
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now().isoformat(timespec="seconds")
    result: dict = {"started_at": started, "dry_run": args.dry_run}
    log(f"=== 知识库同步开始 {started}（匿名游客通道，不使用账号凭证）===")
    try:
        sess = make_session()
        log("  匿名登录 ✔")
        bak = "" if args.dry_run else backup(db)
        if bak:
            log(f"  已备份数据库 → {bak}")
        con = sqlite3.connect(str(db))
        try:
            result["products"] = sync_products(sess, con, now, args.dry_run)
            result["solutions"] = backfill_solutions(con, now, args.dry_run)
            result["schemes"] = sync_schemes(sess, con, args.dry_run)
            stats = {
                "products": con.execute("SELECT COUNT(*) FROM products").fetchone()[0],
                "solutions": con.execute("SELECT COUNT(*) FROM solutions").fetchone()[0],
                "boms": con.execute("SELECT COUNT(*) FROM boms").fetchone()[0],
                "bom_items": con.execute("SELECT COUNT(*) FROM bom_items").fetchone()[0],
            }
            result["stats"] = stats
            con.close()
        except Exception:
            try:
                con.close()
            except Exception:
                pass
            raise
        log(f"  ✔ 最终：产品 {stats['products']} / 方案 {stats['solutions']} / "
            f"配单 {stats['boms']} / 明细 {stats['bom_items']}")
        result["ok"] = True
        log("=== 同步完成 ===")
        log("SYNC_RESULT " + json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        log(f"✗ 同步失败：{result['error']}")
        log("SYNC_RESULT " + json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
