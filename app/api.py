"""Web 服务：需求 → Spec → 拓扑图 / Word（P0 三页流程）。

P0 只暴露三个动作：
  1. POST /api/spec    需求文本 → Spec（后端规则引擎 + 产品库选型）
  2. POST /api/render  审阅后的 Spec → 重新核算 → 拓扑 PNG + Word DOCX
  3. GET  /api/export/{文件名}  下载渲染产物

所有数字仍由 calc.py 确定性计算，前端只做展示与编辑，不参与计算口径。
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from config.settings import BASE_DIR, DB_PATH

from .rag import service as rag_service
from .compare import FIELDS, compare as compare_competitor, export_docx
from . import auth, compare_service, deviations, engineering_checks, metrics, netcheck, sessions, tender
from .builder import SpecGateError
from .quote import build_quote
from agent import service as agent_service
from agent.parser import status as agent_parser_status
from agent.tools import status as agent_tools_status

WEB_DIR = BASE_DIR / "web" / "static"
EXPORT_DIR = BASE_DIR / "data" / "exports"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class RagRequest(BaseModel):
    query: str
    mode: str = "solution"       # solution / competitor / all
    industry: str = ""
    top_k: int = 8


class ComparePair(BaseModel):
    category: str
    dahua: str
    hik: str


class GroupCompareRequest(BaseModel):
    pairs: list[ComparePair]


class AgentStartRequest(BaseModel):
    text: str
    name: str | None = None
    lanes: int | None = None
    retention_days: int | None = None


class AgentResumeRequest(BaseModel):
    answer: str | dict


class SessionRenameRequest(BaseModel):
    title: str


class AgentEditRequest(BaseModel):
    lanes: int | None = None
    retention_days: int | None = None
    name: str | None = None
    devices: list[dict] | None = None


app = FastAPI(title="大华解决方案助手", version="1.1")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

CLIENT_COOKIE = "dh_cid"
AUTH_COOKIE = "dh_auth"
_CLIENT_RE = re.compile(r"^[0-9a-f]{32}$")


@app.middleware("http")
async def _no_cache_pages_and_assets(request, call_next):
    """页面与静态资源一律不缓存。

    之前改了 styles.css 但浏览器仍用旧副本，导致 [hidden] 规则没生效、
    切 Tab 后产品区依旧堆在上面。这里从服务端根除缓存问题。
    """
    resp = await call_next(request)
    path = request.url.path
    if (path in ("/kb", "/rag", "/compare", "/agent")
            or path.startswith("/static/")
            or path.startswith("/kb/solution")):
        resp.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.middleware("http")
async def _client_identity(request: Request, call_next):
    """匿名客户端标识：登录功能上线前，用 HttpOnly cookie 绑定会话与导出文件。

    这层只解决「同一个浏览器能看自己的历史」；P1 接入账号后由 user_id
    做真正的数据隔离，cookie 仅作为未登录访客的兜底。
    """
    cid = request.cookies.get(CLIENT_COOKIE) or ""
    fresh = not _CLIENT_RE.match(cid)
    if fresh:
        cid = uuid.uuid4().hex
    request.state.client_id = cid
    user = auth.user_by_token(request.cookies.get(AUTH_COOKIE) or "")
    request.state.user_id = user["id"] if user else None
    request.state.user = user
    resp = await call_next(request)
    if fresh:
        resp.set_cookie(CLIENT_COOKIE, cid, max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax")
    return resp


def _cid(request: Request) -> str:
    return getattr(request.state, "client_id", "") or ""


def _uid(request: Request) -> int | None:
    return getattr(request.state, "user_id", None)


@app.get("/", include_in_schema=False)
def index():
    """原「工作台」已并入 Agent 工作台，根路径重定向过去。"""
    return RedirectResponse(url="/agent", status_code=307)


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(WEB_DIR / "login.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# 账号：注册 / 登录 / 登出 / 当前用户
# ---------------------------------------------------------------------------

class AuthRequest(BaseModel):
    username: str
    password: str


def _set_auth_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(AUTH_COOKIE, token, max_age=auth.TOKEN_DAYS * 24 * 3600,
                    httponly=True, samesite="lax")


@app.get("/api/auth/me")
def api_auth_me(request: Request) -> dict:
    return {"user": getattr(request.state, "user", None)}


@app.post("/api/auth/register")
def api_auth_register(req: AuthRequest, request: Request, response: Response) -> dict:
    try:
        user = auth.register(req.username, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    token = auth.create_token(user["id"])
    _set_auth_cookie(response, token)
    # 新账号从干净工作区开始：不认领本浏览器的匿名会话，并换发新的匿名标识，
    # 否则刚注册就会看到注册前的匿名案例（历史列表与自动恢复都会带出来）。
    response.set_cookie(CLIENT_COOKIE, uuid.uuid4().hex, max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax")
    metrics.record("register", _cid(request), None)
    return {"user": user, "claimed": {"sessions": 0, "exports": 0}}


@app.post("/api/auth/login")
def api_auth_login(req: AuthRequest, request: Request, response: Response) -> dict:
    try:
        user = auth.authenticate(req.username, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={"message": str(exc)}) from exc
    token = auth.create_token(user["id"])
    _set_auth_cookie(response, token)
    claimed = sessions.claim(_cid(request), user["id"])
    metrics.record("login", _cid(request), None)
    return {"user": user, "claimed": claimed}


@app.post("/api/auth/logout")
def api_auth_logout(request: Request, response: Response) -> dict:
    auth.delete_token(request.cookies.get(AUTH_COOKIE) or "")
    response.delete_cookie(AUTH_COOKIE)
    return {"ok": True}


@app.get("/compare", include_in_schema=False)
def compare_page() -> FileResponse:
    return FileResponse(WEB_DIR / "compare.html")


@app.get("/api/compare")
def api_compare(category: str = "网络摄像机") -> dict:
    if category not in FIELDS:
        raise HTTPException(status_code=404, detail={"message": f"不支持的对比类目：{category}", "gaps": []})
    try:
        official = compare_service.OFFICIAL_PAIRS.get(category)
        # 补采后同类目有几十款机型，compare() 内部按 field_label 覆盖会串型号；
        # 旧路由固定返回「官网同档代表款」这一组，保证结果稳定可回溯。
        if official:
            return compare_service.pair(category, official[0], official[1])
        return compare_competitor(category)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={"message": str(exc), "gaps": []}) from exc


# ---------------------------------------------------------------------------
# 竞品对比（新）：类目 → 双方产品池 → 各选一款 → 逐字段对比
# ---------------------------------------------------------------------------

@app.get("/api/compare/categories")
def api_compare_categories() -> dict:
    """三个类目及双方可选产品数 / 参数覆盖情况（前端引导页用）。"""
    return compare_service.categories()


@app.get("/api/compare/products")
def api_compare_products(
    category: str, brand: str, q: str = "", offset: int = 0, limit: int = 20
) -> dict:
    return compare_service.products(category, brand, q, offset, limit)


@app.get("/api/compare/pair")
def api_compare_pair(category: str, dahua: str, hik: str) -> dict:
    try:
        return compare_service.pair(category, dahua, hik)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "gaps": []}) from exc


def _pair_result(category: str, dahua: str, hik: str) -> dict:
    if category not in FIELDS:
        raise ValueError(f"不支持的对比类目：{category}")
    return compare_service.pair(category, dahua, hik)


@app.get("/api/compare/docx")
async def api_compare_docx(
    category: str = "网络摄像机", dahua: str = "", hik: str = ""
) -> FileResponse:
    """单组对比导出。传 dahua/hik 时导出自定义配对，否则导出官网代表款对比。"""
    stamp = datetime.now().strftime("%H%M%S")
    out = EXPORT_DIR / f"竞品对比-{_safe_name(category)}-{stamp}.docx"

    def build() -> None:
        if dahua and hik:
            result = _pair_result(category, dahua, hik)
        else:
            official = compare_service.OFFICIAL_PAIRS.get(category)
            result = (_pair_result(category, official[0], official[1])
                      if official else compare_competitor(category))
        export_docx(result, out)

    try:
        await run_in_threadpool(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "gaps": []}) from exc
    return FileResponse(out, media_type=DOCX_MIME, filename=out.name)


@app.post("/api/compare/group/docx")
async def api_compare_group_docx(req: GroupCompareRequest) -> FileResponse:
    """整组对比导出：IPC / NVR / PoE 三组结果合并成一份 DOCX。"""
    if not req.pairs:
        raise HTTPException(status_code=400, detail={"message": "没有可导出的对比组"})
    stamp = datetime.now().strftime("%H%M%S")
    out = EXPORT_DIR / f"竞品对比-整组-{stamp}.docx"

    def build() -> None:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Cm

        from .word import ACCENT, MUTED, _heading, _para, _table

        results = [_pair_result(p.category, p.dahua, p.hik) for p in req.pairs]
        doc = Document()
        sec = doc.sections[0]
        sec.left_margin = sec.right_margin = Cm(2)
        _para(doc, "竞品对比 · 典型监控系统整组", 20, True, ACCENT,
              WD_ALIGN_PARAGRAPH.CENTER, 2)
        _para(doc, "大华 vs 海康 · 三类设备各选一款（官网公开参数）", 11, False,
              MUTED, WD_ALIGN_PARAGRAPH.CENTER, 8)
        for r in results:
            _heading(doc, f"{r['category']}：大华 {r['dahua_model']} vs 海康 {r['hik_model']}")
            s = r["summary"]
            _para(doc, f"共 {len(r['rows'])} 个可比字段：同档 {s['same']} 项、大华占优 {s['dahua']} 项、"
                       f"海康占优 {s['hik']} 项、口径不同 {s['qualitative']} 项、仅一方公开 "
                       f"{s['only_dahua'] + s['only_hik']} 项。", 10, space_after=4)
            _table(doc, ["字段", "大华（归一化）", "海康（归一化）", "判定", "差异 / 口径说明"],
                   [[x["field"], x["dahua_value"], x["hik_value"], x["status_label"],
                     "；".join(y for y in (x["delta"], x["note"]) if y)] for x in r["rows"]],
                   [2.6, 4.2, 4.2, 2.0, 4.0])
            _para(doc, r["disclaimer"], 8.5, False, MUTED, space_after=8)
        _para(doc, "本表由程序从双方官网公开参数页 / 本地产品库生成，仅作售前参考；"
                    "缺失项不作推断，交付前须人工复核。", 8.5, False, MUTED, space_after=0)
        doc.save(out)

    try:
        await run_in_threadpool(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc), "gaps": []}) from exc
    return FileResponse(out, media_type=DOCX_MIME, filename=out.name)


@app.get("/api/export/{filename}")
def export_file(filename: str, request: Request, inline: int = 0) -> FileResponse:
    """导出下载：防路径穿越 + 归属校验（文件由哪个客户端生成，就只有它能下载）。"""
    safe = Path(filename).name
    if safe != filename or not safe:
        raise HTTPException(status_code=404, detail={"message": "文件不存在或已过期", "gaps": []})
    root = EXPORT_DIR.resolve()
    path = (root / safe).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail={"message": "文件不存在或已过期", "gaps": []})
    if not sessions.owns_file(_cid(request), safe, _uid(request)):
        raise HTTPException(status_code=403, detail={"message": "无权访问该文件", "gaps": []})
    if not inline:
        metrics.record("export", _cid(request), None, filename=safe)
    media = {
        ".docx": DOCX_MIME,
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".zip": "application/zip",
        ".pdf": "application/pdf",
    }.get(path.suffix.lower(), "image/png")
    if inline:
        return FileResponse(path, media_type=media)
    return FileResponse(path, media_type=media, filename=safe)


# ---------------------------------------------------------------------------
# 知识库可视化：统计 + 联网同步（大华云商最新产品 / 方案）
# ---------------------------------------------------------------------------

SYNC_JOBS: dict[str, dict] = {}
_SYNC_LOCK = threading.Lock()


def _kb_stats() -> dict:
    """汇总知识库体量、分布与最近同步时间。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    def one(sql: str):
        row = conn.execute(sql).fetchone()
        return row[0] if row else 0

    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql).fetchall()]

    try:
        products = one("SELECT COUNT(*) FROM products")
        solutions = one("SELECT COUNT(*) FROM solutions")
        cases = one("SELECT COUNT(*) FROM pages")
        competitor = one("SELECT COUNT(*) FROM competitor_facts")
        competitor_hik = one("SELECT COUNT(*) FROM competitor_facts WHERE brand='海康'")
        competitor_models = one("SELECT COUNT(DISTINCT brand || '|' || model) FROM competitor_facts")
        competitor_hik_models = one("SELECT COUNT(DISTINCT model) FROM competitor_facts WHERE brand='海康'")
        boms = one("SELECT COUNT(*) FROM boms")
        bom_items = one("SELECT COUNT(*) FROM bom_items")
        solutions_with_bom = one(
            "SELECT COUNT(*) FROM solutions s JOIN boms b ON b.scheme_id=s.sol_id"
        )
        cases_fetched_at = one("SELECT MAX(fetched_at) FROM pages")
        last_product_sync = one("SELECT MAX(collected_at) FROM products")
        last_solution_sync = one("SELECT MAX(collected_at) FROM solutions")

        product_categories = rows(
            "SELECT class_name AS name, COUNT(*) AS c FROM products "
            "GROUP BY class_name ORDER BY c DESC LIMIT 14"
        )
        solution_scenes = rows(
            "SELECT COALESCE(scene_name,'未知') AS name, COUNT(*) AS c "
            "FROM solutions GROUP BY scene_name ORDER BY c DESC"
        )
        product_directions = rows(
            "SELECT COALESCE(direction,'未知') AS name, COUNT(*) AS c "
            "FROM products GROUP BY direction ORDER BY c DESC"
        )
    finally:
        conn.close()

    return {
        "products": products,
        "solutions": solutions,
        "cases": cases,
        "competitor": competitor,
        "competitor_hik": competitor_hik,
        "competitor_models": competitor_models,
        "competitor_hik_models": competitor_hik_models,
        "boms": boms,
        "bom_items": bom_items,
        "solutions_with_bom": solutions_with_bom,
        "cases_fetched_at": cases_fetched_at,
        "last_product_sync": last_product_sync,
        "last_solution_sync": last_solution_sync,
        "product_categories": product_categories,
        "solution_scenes": solution_scenes,
        "product_directions": product_directions,
    }


def _run_sync_script(module: str, job: dict) -> int:
    """跑同步脚本并返回真实退出码；同时捕获脚本打印的 SYNC_RESULT 结构化结果。"""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for raw in proc.stdout:
            with _SYNC_LOCK:
                job["log"] += raw
                if raw.startswith("SYNC_RESULT "):
                    try:
                        job["result"] = json.loads(raw[len("SYNC_RESULT "):].strip())
                    except (ValueError, TypeError):
                        pass
        try:
            proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            with _SYNC_LOCK:
                job["log"] += "\n[ERROR] 脚本运行超时（>15min）已被终止\n"
            return 1
        return int(proc.returncode or 0)
    except Exception as exc:  # noqa: BLE001
        with _SYNC_LOCK:
            job["log"] += f"\n[ERROR] {exc}\n"
        return 1


def _sync_worker(job_id: str) -> None:
    job = SYNC_JOBS.get(job_id)
    if not job:
        return
    with _SYNC_LOCK:
        job["log"] = "▶ 开始同步：大华云商公开产品 / 方案清单 / 配单（匿名游客接口，真实增量）\n"
    if not netcheck.mall_reachable(force=True):
        with _SYNC_LOCK:
            job["log"] += (
                "✗ 当前无网络连接：同步需要访问大华云商公开接口，已跳过，未发起任何重试。\n"
                "本地知识库数据未受影响；产品查询、智能问答、竞品对比、拓扑图与 Word 导出"
                "均可继续离线使用。\n"
                "恢复联网后再点一次「同步最新大华产品与方案」即可。\n"
            )
            job["status"] = "failed"
            job["exit_code"] = -1
            job["stats"] = _kb_stats()
            job["result"] = {"ok": False, "error": "离线：无法连接 mall.dahuatech.com"}
        return
    code = _run_sync_script("scraper.sync_latest", job)
    with _SYNC_LOCK:
        result = job.get("result") or {}
        job["exit_code"] = code
        job["stats"] = _kb_stats()
        ok = code == 0 and bool(result.get("ok"))
        job["status"] = "done" if ok else "failed"
        if ok:
            job["log"] += "\n✔ 同步成功（数据已真实写入本地知识库）\n"
        else:
            job["log"] += ("\n✗ 同步失败：脚本返回非 0 或结果异常。"
                           "数据库在写入前已自动备份，请查看上方错误原因。\n")


# ---------------------------------------------------------------------------
# RAG：混合检索 + 来源标注反幻觉（对应《华为云方案助手融入改造清单》Step 1~3）
# ---------------------------------------------------------------------------
@app.get("/rag", include_in_schema=False)
def rag_page() -> FileResponse:
    return FileResponse(WEB_DIR / "rag.html")


@app.get("/api/rag/status")
def rag_status() -> dict:
    """索引规模、各子集数量、当前生成引擎（有 Key / 零 Key 降级）。"""
    return rag_service.status()


@app.post("/api/rag/reindex")
def rag_reindex() -> dict:
    """数据库更新后重建内存索引（服务内部也会按 DB mtime 自动重建）。"""
    return rag_service.reindex()


@app.post("/api/rag/ask")
def rag_ask(req: RagRequest) -> dict:
    """提问 → 混合检索 → 带 [资料N] 的回答 + 可点击来源卡片。"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail={"message": "问题不能为空"})
    return rag_service.ask(
        query=req.query,
        mode=req.mode,
        industry=req.industry,
        top_k=max(1, min(req.top_k, 20)),
    )


# ---------------------------------------------------------------------------
# Agent 工作台（P1）：LangGraph 只承担 checkpoint / human-in-loop / 条件回边，
# 计算与选型仍由 app.builder / app.calc 确定性执行。
# ---------------------------------------------------------------------------

@app.get("/agent", include_in_schema=False)
def agent_page() -> FileResponse:
    return FileResponse(WEB_DIR / "agent.html")


@app.get("/api/agent/sessions")
def api_agent_sessions(request: Request, limit: int = 50, offset: int = 0) -> dict:
    """当前客户端 / 当前账号的历史会话（按最近活动倒序）。"""
    return sessions.list_sessions(_cid(request), limit, offset, _uid(request))


@app.patch("/api/agent/sessions/{thread_id}")
def api_agent_session_rename(thread_id: str, req: SessionRenameRequest,
                             request: Request) -> dict:
    try:
        ok = sessions.rename_session(_cid(request), thread_id, req.title, _uid(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    if not ok:
        raise HTTPException(status_code=404, detail={"message": "会话不存在"})
    return {"ok": True, "thread_id": thread_id, "title": req.title.strip()}


@app.delete("/api/agent/sessions/{thread_id}")
def api_agent_session_delete(thread_id: str, request: Request) -> dict:
    """删除会话：索引 + LangGraph checkpoint + 该会话产物文件一并清理。"""
    cid = _cid(request)
    info = sessions.delete_session(cid, thread_id, _uid(request))
    if info is None:
        raise HTTPException(status_code=404, detail={"message": "会话不存在"})
    try:
        agent_service.delete_thread(thread_id)
    except Exception:  # noqa: BLE001 —— checkpoint 清理失败不影响索引已删除
        pass
    removed = 0
    for name in info["files"]:
        path = EXPORT_DIR / Path(name).name
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            pass
    metrics.record("session_delete", cid, thread_id)
    return {"ok": True, "thread_id": thread_id, "removed_files": removed}


@app.get("/api/agent/sessions/{thread_id}/versions")
def api_agent_session_versions(thread_id: str, request: Request) -> dict:
    """方案版本快照：每次「通过并生成」新增一版，可回溯下载历史交付物。"""
    items = sessions.list_versions(_cid(request), thread_id, _uid(request))
    if items is None:
        raise HTTPException(status_code=404, detail={"message": "会话不存在"})
    return {"items": items}


@app.get("/api/agent/status")
def api_agent_status() -> dict:
    """当前需求解析引擎 + 工具核实层状态（断网自动降级，流程不受影响）。"""
    return {"ok": True, **agent_parser_status(), "tools": agent_tools_status()}


@app.post("/api/tender/analyze")
async def api_tender_analyze(file: UploadFile = File(...)) -> dict:
    """先读标再建方案：把招标文件解析成可直接回填到工作台的需求草稿。

    这是售前真实工作流的入口。解析只抽取原文中能确认的内容，数字不做猜测；
    生成的 draft 仍需用户审阅后再启动 Agent。
    """
    raw = await file.read()
    try:
        body = tender.read_text(file.filename or "招标文件", raw)
    except tender.TenderError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    if not body.strip():
        raise HTTPException(status_code=400, detail={"message": "文件没有可读取的文字内容"})
    analysis = tender.analyze(body)
    project = analysis.get("project") or {}
    stars = analysis.get("stars") or []
    clauses = analysis.get("clauses") or []
    parts = []
    if project.get("name"):
        parts.append(f"项目：{project['name']}")
    if project.get("site"):
        parts.append(f"地点：{project['site']}")
    if project.get("duration"):
        parts.append(f"工期：{project['duration']}")
    if stars:
        parts.append("重点技术要求：" + "；".join(x["text"] for x in stars[:12]))
    elif clauses:
        parts.append("技术要求：" + "；".join(x["text"] for x in clauses[:12]))
    draft = "。".join(parts).strip("。")
    if not draft:
        draft = "请根据招标文件中的技术要求，梳理视频监控点位、存储天数、平台和网络配置。"
    return {
        "filename": file.filename or "招标文件",
        "draft": draft + "。",
        "project": project,
        "stars": stars,
        "clauses": clauses,
        "scoring": analysis.get("scoring") or [],
        "commercial": analysis.get("commercial") or [],
        "warnings": analysis.get("warnings") or [],
        "stats": analysis.get("stats") or {},
    }


def _guard_thread(request: Request, thread_id: str) -> None:
    """会话归属校验：既不属于本浏览器、也不属于当前账号的 thread 按不存在处理。"""
    owner = sessions.owner_of(thread_id)
    if owner is None:
        return
    if owner["client_id"] == _cid(request):
        return
    uid = _uid(request)
    if uid is not None and owner["user_id"] == str(uid):
        return
    raise HTTPException(status_code=404, detail={"message": "会话不存在或已过期"})


def _attach_product_evidence(data: dict) -> dict:
    """给审阅页的每个型号补上产品库来源，便于人工点击核对。"""
    spec = data.get("spec") or {}
    devices = (spec.get("devices") or []) if isinstance(spec, dict) else []
    models = [d.get("model") for d in devices if d.get("model")]
    if not models:
        return data
    conn = sqlite3.connect(str(DB_PATH))
    try:
        marks = ",".join("?" for _ in models)
        rows = conn.execute(
            f"SELECT model, source_url, collected_at FROM products WHERE model IN ({marks})",
            models,
        ).fetchall()
    finally:
        conn.close()
    evidence = {r[0]: {"url": r[1] or "", "collected_at": r[2] or ""} for r in rows}
    for device in devices:
        ev = evidence.get(device.get("model"))
        if ev:
            device["source_url"] = ev["url"]
            device["source_collected_at"] = ev["collected_at"]
    return data


def _attach_engineering_validation(data: dict) -> dict:
    if data.get("spec"):
        data["validation"] = engineering_checks.evaluate(data["spec"], data.get("text") or "")
    return data


@app.post("/api/agent/start")
def api_agent_start(req: AgentStartRequest, request: Request) -> dict:
    try:
        data = agent_service.start(
            req.text, name=req.name, lanes=req.lanes,
            retention_days=req.retention_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    data = _attach_engineering_validation(_attach_product_evidence(data))
    cid = _cid(request)
    sessions.record(cid, data, user_id=_uid(request))
    metrics.record(metrics.START, cid, data.get("thread_id"))
    return data


@app.get("/api/agent/{thread_id}/state")
def api_agent_state(thread_id: str, request: Request) -> dict:
    _guard_thread(request, thread_id)
    try:
        data = agent_service.state_of(thread_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"message": "会话不存在或已过期"}) from exc
    data = _attach_engineering_validation(_attach_product_evidence(data))
    # 只读刷新不改变最近活动时间，避免历史列表顺序被打乱
    sessions.record(_cid(request), data, touch=False, user_id=_uid(request))
    return data


@app.post("/api/agent/{thread_id}/resume")
def api_agent_resume(thread_id: str, req: AgentResumeRequest, request: Request) -> dict:
    _guard_thread(request, thread_id)
    # 正式生成前拦截高风险工程问题；普通复核/修改仍可继续。
    if isinstance(req.answer, dict) and str(req.answer.get("decision") or "").lower() in {
        "approve", "approved", "ok", "yes", "y", "通过", "同意", "确认"
    }:
        try:
            current = agent_service.state_of(thread_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"message": "会话不存在或已过期"}) from exc
        validation = engineering_checks.evaluate(current.get("spec") or {}, current.get("text") or "")
        if validation.get("level") == "block":
            blockers = [x["title"] for x in validation.get("items") or [] if x.get("level") == "block"]
            raise HTTPException(status_code=409, detail={
                "message": "方案存在必须先处理的工程问题：" + "、".join(blockers),
                "validation": validation,
            })
    try:
        data = agent_service.resume(thread_id, req.answer)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"message": "会话不存在或已结束"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    data = _attach_engineering_validation(_attach_product_evidence(data))
    cid = _cid(request)
    sessions.record(cid, data, user_id=_uid(request))
    if data.get("status") == "done":
        metrics.record(metrics.APPROVE, cid, thread_id)
    return data


@app.get("/api/agent/{thread_id}/quote")
def api_agent_quote(thread_id: str, request: Request) -> dict:
    """报价单：按当前 Spec 汇总标准价（前端与 Excel/PPT 导出共用同一口径）。"""
    _guard_thread(request, thread_id)
    try:
        data = agent_service.state_of(thread_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"message": "会话不存在或已过期"}) from exc
    if not data.get("spec"):
        raise HTTPException(status_code=400, detail={"message": "会话还没有生成 Spec"})
    return build_quote(data["spec"])


@app.post("/api/agent/{thread_id}/edit")
def api_agent_edit(thread_id: str, req: AgentEditRequest, request: Request) -> dict:
    """在线编辑：改路数 / 存储天数 / 项目名 / 换型号 / 改数量，确定性重算后重新渲染。"""
    _guard_thread(request, thread_id)
    patch = {k: v for k, v in req.model_dump().items() if v not in (None, "", [])}
    if not patch:
        raise HTTPException(status_code=400, detail={"message": "没有需要保存的修改"})
    try:
        data = agent_service.edit_outputs(thread_id, patch)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"message": "会话不存在或已过期"}) from exc
    except (ValueError, SpecGateError) as exc:
        gaps = getattr(exc, "gaps", None) or [str(exc)]
        raise HTTPException(status_code=400,
                            detail={"message": "；".join(gaps), "gaps": gaps}) from exc
    data = _attach_engineering_validation(_attach_product_evidence(data))
    cid = _cid(request)
    sessions.record(cid, data, user_id=_uid(request))
    metrics.record("edit", cid, thread_id, fields=sorted(patch.keys()))
    return data


@app.post("/api/agent/{thread_id}/pdf")
def api_agent_pdf(thread_id: str, request: Request) -> FileResponse:
    """把当前 Word 方案转成 PDF（Windows + Microsoft Word）。"""
    _guard_thread(request, thread_id)
    try:
        data, pdf = agent_service.export_pdf(thread_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail={"message": "会话还没有生成 Word 方案"}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"message": str(exc)}) from exc
    cid = _cid(request)
    sessions.record(cid, data, user_id=_uid(request), version=False)
    metrics.record("export", cid, thread_id, kind="pdf")
    return FileResponse(pdf, media_type="application/pdf", filename=pdf.name)


@app.post("/api/agent/{thread_id}/deviations")
async def api_agent_deviations(
    thread_id: str,
    request: Request,
    text: str = Form(""),
    file: UploadFile | None = File(None),
) -> dict:
    """技术偏离表：粘贴招标条款，或上传 Excel / 招标文件 PDF / Word。"""
    _guard_thread(request, thread_id)
    try:
        data = agent_service.state_of(thread_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"message": "会话不存在或已过期"}) from exc
    spec = data.get("spec")
    if not spec:
        raise HTTPException(status_code=400, detail={"message": "会话还没有生成 Spec"})

    source = "text"
    analysis: dict | None = None
    if file is not None and file.filename:
        suffix = Path(file.filename).suffix.lower()
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail={"message": "上传文件为空"})
        if suffix in (".xlsx", ".xlsm"):
            source = "excel"
            try:
                clauses = deviations.parse_xlsx(raw)
            except Exception as exc:  # noqa: BLE001 —— 上传文件由用户提供，统一转成 400
                raise HTTPException(
                    status_code=400,
                    detail={"message": f"Excel 解析失败：{type(exc).__name__}"},
                ) from exc
            if not clauses:
                raise HTTPException(
                    status_code=400,
                    detail={"message": "未识别到条款，请确认表头含「条款 / 技术要求」"},
                )
        else:
            source = "tender"
            try:
                body = tender.read_text(file.filename, raw)
            except tender.TenderError as exc:
                raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
            analysis = tender.analyze(body)
            clauses = analysis["clauses"] or deviations.parse_text(body)
            if not clauses:
                raise HTTPException(
                    status_code=400,
                    detail={"message": "没有从文件里抽出技术条款，请确认是招标文件正文"},
                )
    else:
        analysis = tender.analyze(text) if len(text) >= 800 else None
        clauses = (analysis or {}).get("clauses") or deviations.parse_text(text)
        if not clauses:
            raise HTTPException(status_code=400,
                                detail={"message": "请粘贴招标条款，或上传 Excel / 招标文件"})

    rows = deviations.evaluate(clauses, spec)
    summary = deviations.summarize(rows)
    scores = deviations.score_index(rows)
    project_name = (spec.get("project") or {}).get("name") or "未命名方案"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")[:-3]
    name = f"{_safe_name(project_name)}-{stamp}-技术偏离表.xlsx"
    deviations.render_xlsx(rows, summary, project_name, scores, EXPORT_DIR / name)

    cid = _cid(request)
    sessions.register_exports(cid, thread_id, [name], _uid(request))
    sessions.set_stage(cid, thread_id, "投标技术复核", _uid(request))
    metrics.record("deviations", cid, thread_id, count=len(rows), source=source)
    result = {
        "rows": rows,
        "summary": summary,
        "scores": scores,
        "file": {"name": name, "url": "/api/export/" + quote(name)},
    }
    if analysis is not None:
        # clauses 已经转成 rows，不再重复回传，避免响应体过大
        result["tender"] = {k: v for k, v in analysis.items() if k != "clauses"}
    return result


@app.get("/api/stats/overview")
def api_stats_overview(request: Request) -> dict:
    """当前客户端的使用概览（启动数 / 完成数 / 导出数 / 完成率）。"""
    return metrics.overview(_cid(request))


@app.get("/kb", include_in_schema=False)
def kb_page() -> FileResponse:
    return FileResponse(WEB_DIR / "kb.html")


@app.get("/api/kb/stats")
def kb_stats() -> dict:
    return _kb_stats()


def _kb_query(sql: str, params: list = None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
    finally:
        conn.close()


@app.get("/api/kb/products")
def kb_products(q: str = "", class_name: str = "", offset: int = 0, limit: int = 24) -> dict:
    """列出知识库真实产品（带图片与详情），支持关键词 / 类目筛选与分页。"""
    limit = max(1, min(limit, 60))
    offset = max(0, offset)
    where, params = [], []
    if q:
        where.append("(model LIKE ? OR display_name LIKE ? OR part_num LIKE ? OR class_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
    if class_name:
        where.append("class_name = ?")
        params.append(class_name)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    base = (
        "SELECT model, display_name, price_stand, class_name, "
        "COALESCE(img_local, img_url) AS img_url, part_num, "
        "direction, year_new, pi_status, pi_sysj, prod_id, prod_desc, spec_value, "
        "source, source_url "
        f"FROM products{where_sql} ORDER BY class_name, model LIMIT ? OFFSET ?"
    )
    rows = _kb_query(base, params + [limit, offset])
    total = _kb_query(
        f"SELECT COUNT(*) AS c FROM products{where_sql}", params
    )[0]["c"]
    return {"items": rows, "total": total, "offset": offset, "limit": limit}


@app.get("/api/kb/categories")
def kb_categories() -> dict:
    """产品类目树：返回每个 class_name 的产品数量。"""
    rows = _kb_query(
        "SELECT class_name AS name, COUNT(*) AS c FROM products "
        "GROUP BY class_name ORDER BY c DESC"
    )
    return {"items": rows, "total": sum(r["c"] for r in rows)}


@app.get("/api/kb/solutions")
def kb_solutions(q: str = "", limit: int = 200) -> dict:
    """解决方案 = 官网方案 + 云商配单（已幂等补齐为 53 条）。"""
    limit = max(1, min(limit, 300))
    where, params = "", []
    if q:
        where = " WHERE (s.name LIKE ? OR s.scene_name LIKE ? OR b.industry LIKE ?)"
        params = [f"%{q}%"] * 3
    rows = _kb_query(
        "SELECT s.sol_id, s.name, COALESCE(s.scene_name,'') AS scene_name, "
        "COALESCE(s.img_local, s.detail_img) AS detail_img, "
        "COALESCE(s.release_time,'') AS release_time, "
        "COALESCE(s.summary,'') AS summary, s.collected_at, "
        "(SELECT COUNT(*) FROM bom_items i WHERE i.scheme_id=s.sol_id) AS item_count, "
        "CASE WHEN b.scheme_id IS NULL THEN '官网方案' ELSE '云商官方配单' END AS source, "
        "COALESCE(b.industry, s.scene_name, '') AS industry "
        "FROM solutions s LEFT JOIN boms b ON b.scheme_id=s.sol_id"
        + where +
        " ORDER BY item_count DESC, s.release_time DESC, s.name LIMIT ?",
        params + [limit],
    )
    total = _kb_query(
        "SELECT COUNT(*) AS c FROM solutions s LEFT JOIN boms b ON b.scheme_id=s.sol_id" + where,
        params,
    )[0]["c"]
    return {"items": rows, "total": total}


@app.get("/api/kb/solutions/{sol_id}/items")
def kb_solution_items(sol_id: str) -> dict:
    """某方案的云商配单明细（点击方案卡片展开）。"""
    rows = _kb_query(
        "SELECT seq, category, product_name, model, spec, unit, qty, unit_price, "
        "amount, price_basis, match_type, remark FROM bom_items "
        "WHERE scheme_id=? ORDER BY seq LIMIT 200",
        [sol_id],
    )
    total = _kb_query("SELECT COUNT(*) AS c FROM bom_items WHERE scheme_id=?", [sol_id])[0]["c"]
    return {
        "items": rows, "total": total,
        "note": "云商选型池：同一分类下多个型号为可替换候选，不是累加采购清单，总价不可直接相加。",
    }


@app.get("/api/kb/solutions/{sol_id}")
def kb_solution_detail(sol_id: str) -> dict:
    """方案详情页四块：方案详情 / 方案配单 / 相关资料 / 相关内容。

    - 方案详情：名称、场景、发布时间、摘要与云商原站详情 HTML（含图文）。
    - 方案配单：本方案的 bom_items（云商选型池，候选可替换）。
    - 相关资料：配单涉及型号在大华产品库的规格 / 图片 / 价格（型号不在产品目录时用配单原文兜底）。
    - 相关内容：同一 scene_name 下的其它方案。
    严格不暴露 view_count（观看数），符合知识库展示口径。
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT sol_id, name, scene_name, detail_img, release_time, summary, "
            "img_local, raw_json FROM solutions WHERE sol_id=?", [sol_id]
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"message": "方案不存在"})
        row = dict(row)

        raw: dict = {}
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except (ValueError, TypeError):
            raw = {}
        detail = raw.get("detail") or {}
        list_item = raw.get("list_item") or {}
        cover = row.get("img_local") or row.get("detail_img") or ""
        sol = {
            "sol_id": row["sol_id"],
            "name": row["name"],
            "scene_name": row.get("scene_name") or "",
            "release_time": row.get("release_time") or "",
            "summary": row.get("summary") or list_item.get("scheDescribe") or "",
            "cover": cover,
            "detail_html": detail.get("detail") or "",
            "tags": [t.get("tagName") for t in (detail.get("planTags") or [])
                     if t.get("tagName")],
            "video_url": detail.get("tencentFileUrl") or detail.get("huaweiCloudUrl") or "",
            "source": "大华云商公开方案",
        }

        bom_rows = conn.execute(
            "SELECT seq, category, product_name, model, spec, unit, qty, unit_price, "
            "amount, price_basis, match_type, remark FROM bom_items "
            "WHERE scheme_id=? ORDER BY seq LIMIT 200", [sol_id]
        ).fetchall()
        bom = {
            "items": [dict(r) for r in bom_rows],
            "total": len(bom_rows),
            "note": "云商选型池：同一分类下多个型号为可替换候选，不是累加采购清单，总价不可直接相加。",
        }

        # 相关资料：配单涉及型号 → 产品库规格 / 图片 / 价格
        models, seen = [], set()
        for r in bom_rows:
            m = r["model"]
            if m and m not in seen:
                seen.add(m)
                models.append(m)
        materials = []
        for m in models:
            p = conn.execute(
                "SELECT model, display_name, price_stand, class_name, "
                "COALESCE(img_local, img_url) AS img, spec_value, prod_desc, source_url "
                "FROM products WHERE model=?", [m]
            ).fetchone()
            if p:
                p = dict(p)
                materials.append({
                    "model": p["model"],
                    "product_name": p["display_name"] or p["model"],
                    "img": p["img"] or "",
                    "price": p["price_stand"],
                    "spec": p["spec_value"] or "",
                    "prod_desc": (p["prod_desc"] or "")[:240],
                    "url": p["source_url"] or "",
                    "class_name": p["class_name"] or "",
                })
            else:
                bi = next((x for x in bom_rows if x["model"] == m), None)
                if bi:
                    materials.append({
                        "model": m,
                        "product_name": bi["product_name"],
                        "img": "",
                        "price": bi["unit_price"],
                        "spec": bi["spec"] or "",
                        "prod_desc": "",
                        "url": "",
                        "class_name": bi["category"] or "",
                    })

        # 相关内容：同场景其它方案
        related = []
        if row.get("scene_name"):
            rel = conn.execute(
                "SELECT s.sol_id, s.name, COALESCE(s.scene_name,'') AS scene_name, "
                "COALESCE(s.img_local, s.detail_img) AS detail_img, "
                "(SELECT COUNT(*) FROM bom_items i WHERE i.scheme_id=s.sol_id) AS item_count, "
                "CASE WHEN b.scheme_id IS NULL THEN '官网方案' ELSE '云商官方配单' END AS source "
                "FROM solutions s LEFT JOIN boms b ON b.scheme_id=s.sol_id "
                "WHERE s.scene_name=? AND s.sol_id!=? ORDER BY item_count DESC LIMIT 6",
                [row["scene_name"], sol_id],
            ).fetchall()
            related = [dict(r) for r in rel]
    finally:
        conn.close()
    return {"solution": sol, "bom": bom, "materials": materials, "related": related}


@app.get("/kb/solution/{sol_id}", include_in_schema=False)
def solution_page(sol_id: str) -> FileResponse:
    return FileResponse(WEB_DIR / "solution.html")


@app.get("/api/kb/cases")
def kb_cases() -> dict:
    """行业案例来自项目最初抓取的大华官网案例页，明确标注抓取时间，不冒充 AI 总结。"""
    rows = _kb_query(
        "SELECT id, industry, title, url, content_len, fetched_at, page_kind, "
        "'大华官网案例页' AS source_name FROM pages "
        "ORDER BY fetched_at DESC, content_len DESC"
    )
    times = [r["fetched_at"] for r in rows if r.get("fetched_at")]
    return {
        "items": rows, "total": len(rows),
        "source_note": "官网案例详情页原文抓取（非 AI 总结、非最新行业研究）",
        "fetched_at": max(times) if times else "",
        "fetched_from": min(times) if times else "",
    }


@app.get("/api/kb/competitors")
def kb_competitors(brand: str = "", category: str = "", limit: int = 60) -> dict:
    """竞品按【机型】聚合展示（补采后海康有 117 款，上限放到 500 防截断）。"""
    limit = max(1, min(limit, 500))
    where, params = [], []
    if brand:
        where.append("brand = ?")
        params.append(brand)
    if category:
        where.append("category = ?")
        params.append(category)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = _kb_query(
        "SELECT brand, category, model, MAX(display_name) AS display_name, "
        "COUNT(*) AS fact_count, MIN(source_url) AS source_url, "
        "MAX(retrieved_at) AS retrieved_at "
        f"FROM competitor_facts{where_sql} GROUP BY brand, category, model "
        "ORDER BY CASE WHEN brand='海康' THEN 0 ELSE 1 END, category, model LIMIT ?",
        params + [limit],
    )
    brands = _kb_query(
        "SELECT brand, COUNT(DISTINCT model) AS models, COUNT(*) AS facts "
        "FROM competitor_facts GROUP BY brand ORDER BY models DESC"
    )
    total = _kb_query(
        f"SELECT COUNT(*) AS c FROM (SELECT 1 FROM competitor_facts{where_sql} "
        "GROUP BY brand, category, model)", params
    )[0]["c"]
    return {"items": rows, "brands": brands, "total": total,
            "note": "统计卡中的『竞品对比』数字是官网参数事实条数，不是产品款数。"}


@app.get("/api/kb/competitors/facts")
def kb_competitor_facts(
    brand: str = "", category: str = "", model: str = "", q: str = "",
    offset: int = 0, limit: int = 40,
) -> dict:
    """逐条开放竞品参数事实（358 条），可按品牌 / 类目 / 机型 / 关键字筛选。

    之前页面只显示"共 358 条"这个数字，用户看不到具体内容；这里把每一条
    官网参数原文 + 来源链接开放出来，海康优先排序。
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where, params = [], []
    for col, val in (("brand", brand), ("category", category), ("model", model)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    if q:
        where.append("(field_label LIKE ? OR raw_value LIKE ? OR model LIKE ? OR section LIKE ?)")
        params += [f"%{q}%"] * 4
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    rows = _kb_query(
        "SELECT id, brand, category, model, display_name, section, field_label, "
        "raw_value, source_url, retrieved_at, page_kind "
        f"FROM competitor_facts{where_sql} "
        "ORDER BY CASE WHEN brand='海康' THEN 0 ELSE 1 END, category, model, id "
        "LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    total = _kb_query(
        f"SELECT COUNT(*) AS c FROM competitor_facts{where_sql}", params
    )[0]["c"]
    brands = _kb_query(
        "SELECT brand, COUNT(*) AS c FROM competitor_facts GROUP BY brand ORDER BY c DESC"
    )
    cats = _kb_query(
        "SELECT category, COUNT(*) AS c FROM competitor_facts GROUP BY category ORDER BY c DESC"
    )
    models = _kb_query(
        "SELECT brand, model, category, COUNT(*) AS c FROM competitor_facts "
        "GROUP BY brand, model, category ORDER BY CASE WHEN brand='海康' THEN 0 ELSE 1 END, model"
    )
    return {
        "items": rows, "total": total, "offset": offset, "limit": limit,
        "filters": {"brands": brands, "categories": cats, "models": models},
        "note": "每一行为一个官网参数字段值；型号来自官网参数页，缺项未收录，不推断、不补数。",
    }


@app.post("/api/kb/sync")
def kb_sync_start() -> dict:
    job_id = uuid.uuid4().hex
    with _SYNC_LOCK:
        SYNC_JOBS[job_id] = {"status": "running", "log": "", "stats": None,
                             "result": None, "exit_code": None}
    threading.Thread(target=_sync_worker, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/kb/sync/{job_id}")
def kb_sync_status(job_id: str) -> dict:
    job = SYNC_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"message": "同步任务不存在", "gaps": []})
    return {"status": job["status"], "log": job["log"], "stats": job["stats"],
            "result": job.get("result"), "exit_code": job.get("exit_code")}


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return cleaned or "未命名方案"
