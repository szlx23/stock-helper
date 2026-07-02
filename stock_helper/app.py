from contextlib import asynccontextmanager
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stock_helper import db
from stock_helper.config import EXCLUDE_FIELDS, FILTER_FIELDS, SCORE_FIELDS, StrategyConfig
from stock_helper.scan_tasks import scan_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="szl的策略助手", lifespan=lifespan)
templates = Jinja2Templates(directory="stock_helper/templates")
app.mount("/static", StaticFiles(directory="stock_helper/static"), name="static")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    config = StrategyConfig()
    task_status = scan_manager.status()
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "summary": db.latest_summary(),
            "candidates": db.latest_candidates(),
            "task_status": task_status,
            "config": config,
            "filter_fields": FILTER_FIELDS,
            "score_fields": SCORE_FIELDS,
            "exclude_fields": EXCLUDE_FIELDS,
        },
    )


@app.get("/candidates", response_class=HTMLResponse)
def candidates(request: Request):
    summary = db.latest_summary()
    return templates.TemplateResponse(
        request,
        "candidates.html",
        {"request": request, "summary": summary, "candidates": db.latest_candidates()},
    )


@app.post("/run-scan")
async def run_scan(request: Request):
    form = await request.form()
    pwd = form.get("run_password", "")
    if pwd != "001023":
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=403)
    config = _config_from_form(form)
    task_id = scan_manager.start(config)
    return JSONResponse({"ok": True, "task_id": task_id})


@app.get("/scan-events")
def scan_events():
    def event_stream():
        offset = 0
        hit_offset = 0
        idle_ticks = 0
        while True:
            task_id, logs, done, offset, progress, new_hits, hit_offset = scan_manager.snapshot(offset, hit_offset)
            for line in logs:
                yield f"data: {json.dumps({'type': 'log', 'task_id': task_id, 'line': line, 'done': False}, ensure_ascii=False)}\n\n"
            if new_hits:
                yield f"data: {json.dumps({'type': 'hits', 'task_id': task_id, 'candidates': new_hits}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'progress', 'task_id': task_id, 'progress': progress, 'done': False}, ensure_ascii=False)}\n\n"
            if done:
                yield f"data: {json.dumps({'type': 'log', 'task_id': task_id, 'line': '任务结束', 'done': True}, ensure_ascii=False)}\n\n"
                break
            idle_ticks += 1
            if idle_ticks > 1800:
                yield f"data: {json.dumps({'type': 'log', 'task_id': task_id, 'line': '连接超时', 'done': True}, ensure_ascii=False)}\n\n"
                break
            time.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/clear-db")
async def clear_db(request: Request):
    body = await request.json()
    if body.get("password") != "001023":
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=403)
    import sqlite3, os
    db_path = db.get_db_path()
    conn = sqlite3.connect(str(db_path))
    for t in ("stock_daily_bars", "candidates", "scan_tasks", "stock_info_cache"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


@app.get("/scan-status")
def scan_status():
    status = scan_manager.status()
    return JSONResponse(
        {
            "task_id": status["task_id"],
            "logs": status["logs"],
            "progress": status["progress"],
            "done": status["done"],
            "summary": _summary_payload(db.latest_summary()),
            "candidates": db.latest_candidates(),
        }
    )


def _config_from_form(form) -> StrategyConfig:
    values = dict(form)
    for name, _, _ in EXCLUDE_FIELDS:
        values[name] = name in form
    return StrategyConfig.from_mapping(values)


def _summary_payload(summary: dict) -> dict:
    scan = summary["scan"]
    return {
        "count": summary["count"],
        "top": summary["top"],
        "params": summary["params"],
        "scan": dict(scan) if scan else None,
    }


def main() -> None:
    import uvicorn

    uvicorn.run("stock_helper.app:app", host="0.0.0.0", port=8501, reload=False)
