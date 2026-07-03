from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stock_helper import db
from stock_helper.config import EXCLUDE_FIELDS, FILTER_FIELDS, SCORE_FIELDS, StrategyConfig
from stock_helper.scan_tasks import ScanInProgressError, scan_manager
from stock_helper.scanner import _market_today


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.fail_running_scans()
    if "STOCK_HELPER_PASSWORD" not in os.environ:
        logging.getLogger("stock_helper").warning(
            "STOCK_HELPER_PASSWORD 未设置，当前使用兼容默认密码；对外部署前必须配置强密码"
        )
    yield


app = FastAPI(title="szl的策略助手", lifespan=lifespan)
templates = Jinja2Templates(directory="stock_helper/templates")
app.mount("/static", StaticFiles(directory="stock_helper/static"), name="static")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'",
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.get("/healthz")
def healthz():
    if not db.health_check():
        return JSONResponse({"status": "unhealthy", "database": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    config = StrategyConfig()
    task_status = scan_manager.status()
    today = _market_today().isoformat()
    summary = db.latest_summary(candidate_date=today)
    task_status["outcome"] = _effective_outcome(task_status, summary)
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "summary": summary,
            "candidates": db.latest_candidates(summary["scan"]["id"], trade_date=today) if summary["scan"] else [],
            "task_status": task_status,
            "config": config,
            "filter_fields": FILTER_FIELDS,
            "score_fields": SCORE_FIELDS,
            "exclude_fields": EXCLUDE_FIELDS,
        },
    )


@app.get("/candidates", response_class=HTMLResponse)
def candidates(request: Request):
    today = _market_today().isoformat()
    summary = db.latest_summary(candidate_date=today)
    return templates.TemplateResponse(
        request,
        "candidates.html",
        {"request": request, "summary": summary, "candidates": db.latest_candidates(trade_date=today)},
    )


@app.post("/run-scan")
async def run_scan(request: Request):
    form = await request.form()
    pwd = form.get("run_password", "")
    if not _password_matches(pwd):
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=403)
    try:
        config = _config_from_form(form)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": f"参数错误：{exc}"}, status_code=422)
    try:
        task_id = scan_manager.start(config)
    except ScanInProgressError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return JSONResponse({"ok": True, "task_id": task_id})


@app.post("/cancel-scan")
async def cancel_scan(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "请求格式错误"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "请求格式错误"}, status_code=400)
    if not _password_matches(body.get("password", "")):
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=403)
    if not scan_manager.cancel():
        return JSONResponse({"ok": False, "error": "当前没有运行中的扫描"}, status_code=409)
    return JSONResponse({"ok": True})


@app.get("/scan-events")
def scan_events():
    def event_stream():
        offset = 0
        hit_offset = 0
        while True:
            task_id, logs, done, offset, progress, new_hits, hit_offset = scan_manager.snapshot(offset, hit_offset)
            for line in logs:
                yield f"data: {json.dumps({'type': 'log', 'task_id': task_id, 'line': line, 'done': False}, ensure_ascii=False)}\n\n"
            if new_hits:
                yield f"data: {json.dumps({'type': 'hits', 'task_id': task_id, 'candidates': new_hits}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'progress', 'task_id': task_id, 'progress': progress, 'done': False}, ensure_ascii=False)}\n\n"
            if done:
                outcome = scan_manager.status()["outcome"]
                yield f"data: {json.dumps({'type': 'log', 'task_id': task_id, 'line': '任务结束', 'done': True, 'outcome': outcome}, ensure_ascii=False)}\n\n"
                break
            time.sleep(0.2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/clear-db")
async def clear_db(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "请求格式错误"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "请求格式错误"}, status_code=400)
    if not _password_matches(body.get("password", "")):
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=403)
    if not scan_manager.status()["done"]:
        return JSONResponse({"ok": False, "error": "扫描运行中，不能清空数据库"}, status_code=409)
    db.clear_all()
    return JSONResponse({"ok": True})


@app.get("/scan-status")
def scan_status():
    status = scan_manager.status()
    today = _market_today().isoformat()
    summary = db.latest_summary(candidate_date=today)
    outcome = _effective_outcome(status, summary)
    return JSONResponse(
        {
            "task_id": status["task_id"],
            "logs": status["logs"],
            "progress": status["progress"],
            "done": status["done"],
            "outcome": outcome,
            "summary": _summary_payload(summary),
            "candidates": db.latest_candidates(summary["scan"]["id"], trade_date=today) if summary["scan"] else [],
        }
    )


def _config_from_form(form) -> StrategyConfig:
    values = dict(form)
    # Compatibility for an older rendered form that could submit max_scan_count
    # into fetch_workers after fields were added/reordered.
    if "fetch_workers" in values and values.get("fetch_workers") == values.get("max_scan_count"):
        try:
            if float(values["fetch_workers"]) > 16:
                values["fetch_workers"] = str(StrategyConfig().fetch_workers)
        except (TypeError, ValueError):
            pass
    for name, _, _ in EXCLUDE_FIELDS:
        values[name] = name in form
    return StrategyConfig.from_mapping(values)


def _password_matches(value: object) -> bool:
    expected = os.getenv("STOCK_HELPER_PASSWORD", "001023")
    return hmac.compare_digest(str(value), expected)


def _summary_payload(summary: dict) -> dict:
    scan = summary["scan"]
    return {
        "count": summary["count"],
        "top": summary["top"],
        "params": summary["params"],
        "scan": dict(scan) if scan else None,
    }


def _effective_outcome(status: dict, summary: dict) -> str:
    if status["outcome"] != "idle":
        return status["outcome"]
    scan = summary.get("scan")
    if scan and scan["status"] in {"running", "success", "failed", "cancelled"}:
        return scan["status"]
    return "idle"


def main() -> None:
    import uvicorn

    uvicorn.run("stock_helper.app:app", host="0.0.0.0", port=8501, reload=False)
