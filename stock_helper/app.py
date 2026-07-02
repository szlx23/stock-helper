from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stock_helper import db
from stock_helper.config import FILTER_FIELDS, SCORE_FIELDS, StrategyConfig
from stock_helper.scanner import run_baostock_scan


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="A股短线策略辅助系统", lifespan=lifespan)
templates = Jinja2Templates(directory="stock_helper/templates")
app.mount("/static", StaticFiles(directory="stock_helper/static"), name="static")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    config = StrategyConfig()
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "summary": db.latest_summary(),
            "config": config,
            "filter_fields": FILTER_FIELDS,
            "score_fields": SCORE_FIELDS,
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
def run_scan(
    max_price: float = Form(40.0),
    near_ma10_pct: float = Form(0.03),
    max_ma10_ma20_gap: float = Form(0.12),
    min_big_yang_pct: float = Form(0.045),
    big_vol_multiple: float = Form(1.6),
    shrink_vol_ratio: float = Form(1.0),
    burst_vol_ratio: float = Form(1.25),
    limit_up_pct: float = Form(0.095),
    max_recent_rise: float = Form(0.65),
    lookback_days: int = Form(160),
    exclude_st: bool = Form(True),
    exclude_bj: bool = Form(True),
    score_yin_line: int = Form(10),
    score_shrink_volume: int = Form(15),
    score_near_ma10: int = Form(20),
    score_ma_bull: int = Form(15),
    score_ma_short_ok: int = Form(10),
    score_ma10_up: int = Form(10),
    score_ma10_ma20_gap_ok: int = Form(10),
    score_above_ma20: int = Form(10),
    score_big_yang: int = Form(15),
    score_limit_up: int = Form(5),
    score_recent_rise_too_high: int = Form(-15),
):
    config = StrategyConfig(
        max_price=max_price,
        near_ma10_pct=near_ma10_pct,
        max_ma10_ma20_gap=max_ma10_ma20_gap,
        min_big_yang_pct=min_big_yang_pct,
        big_vol_multiple=big_vol_multiple,
        shrink_vol_ratio=shrink_vol_ratio,
        burst_vol_ratio=burst_vol_ratio,
        limit_up_pct=limit_up_pct,
        max_recent_rise=max_recent_rise,
        lookback_days=lookback_days,
        exclude_st=exclude_st,
        exclude_bj=exclude_bj,
        score_yin_line=score_yin_line,
        score_shrink_volume=score_shrink_volume,
        score_near_ma10=score_near_ma10,
        score_ma_bull=score_ma_bull,
        score_ma_short_ok=score_ma_short_ok,
        score_ma10_up=score_ma10_up,
        score_ma10_ma20_gap_ok=score_ma10_ma20_gap_ok,
        score_above_ma20=score_above_ma20,
        score_big_yang=score_big_yang,
        score_limit_up=score_limit_up,
        score_recent_rise_too_high=score_recent_rise_too_high,
    )
    scan_id = db.create_scan(config)
    try:
        candidates = run_baostock_scan(config)
    except Exception as exc:
        db.finish_scan(scan_id, 0, "failed", str(exc))
        return RedirectResponse("/", status_code=303)
    db.replace_candidates(scan_id, candidates)
    db.finish_scan(scan_id, len(candidates))
    return RedirectResponse("/candidates", status_code=303)


def main() -> None:
    import uvicorn

    uvicorn.run("stock_helper.app:app", host="0.0.0.0", port=8501, reload=False)
