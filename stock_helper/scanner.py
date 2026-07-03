from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from stock_helper import db
from stock_helper.config import StrategyConfig
from stock_helper.data import StockInfo
from stock_helper.data.multi_provider import make_multi_provider
import random

_SEED = 42
random.seed(_SEED)

from stock_helper.indicators import enrich_history
from stock_helper.strategy import evaluate_stock


class ScanCancelled(Exception):
    pass


class StockScanner:
    def __init__(self) -> None:
        pass

    def run(self, config: StrategyConfig, log=None, progress=None, stop_event=None) -> list[dict]:
        provider = make_multi_provider(log=log)
        opened = provider.__enter__()
        try:
            stocks = opened.list_stocks()
            source = provider.source_name
            if not stocks:
                # 重试一次（BaoStock 偶发返回空）
                _log(log, f"{source} 返回空列表，1秒后重试...")
                import time
                time.sleep(1)
                stocks = opened.list_stocks()
            if not stocks:
                raise RuntimeError(f"{source} 返回空股票列表，数据源可能不可用")
            db.upsert_stock_list(stocks)
            _log(log, f"股票列表已更新（{source}）：{len(stocks)} 只")

            stocks = [stock for stock in stocks if not _excluded_by_code_or_name(stock, config)]
            _log(log, f"读取并预过滤股票：{len(stocks)} 只")
            if len(stocks) > config.max_scan_count:
                stocks = random.sample(stocks, config.max_scan_count)
                _log(log, f"随机采样 {config.max_scan_count} 只进行分析")
            _log(log, f"数据阶段：逐只拉取K线并缓存（数据源：{source}）")
            _log(log, f"K线策略：保留 {config.lookback_days} 日，增量补缺至当日")
            histories = self._prepare_histories(stocks, config, opened, log, progress, stop_event)
        finally:
            provider.__exit__(None, None, None)

        _log(log, f"分析阶段：并行线程数 {config.max_workers}")
        return self._analyze_histories(stocks, histories, config, log, progress, stop_event)

    def _prepare_histories(self, stocks, config, provider, log=None, progress=None, stop_event=None) -> dict[str, list[dict]]:
        histories: dict[str, list[dict]] = {}
        skipped = 0
        fetch_fails = 0
        total = len(stocks)
        for idx, stock in enumerate(stocks, start=1):
            if stop_event is not None and stop_event.is_set():
                raise ScanCancelled("扫描已取消")
            _progress(
                progress,
                phase="data",
                completed=idx - 1,
                total=total,
                hits=0,
                current_code=stock.code,
                current_name=stock.name,
            )
            bars_before = len(db.get_cached_bars(stock.code, config.lookback_days))
            history = ensure_history_cached(stock.code, config, provider)
            bars_after = len(history)
            if bars_before < 35 and bars_after < 35:
                fetch_fails += 1
            if bars_after >= 35:
                histories[stock.code] = history
            else:
                skipped += 1
            if idx == 1:
                _log(log, f"数据阶段：逐只拉取K线并缓存（共{total}只）")
            if idx == total or idx % 50 == 0 or (idx <= 10 and idx % 5 == 0):
                _log(log, f"数据进度 {idx}/{total} ({idx*100//total}%)，可分析 {len(histories)}，跳过 {skipped}，拉取失败 {fetch_fails}")
        _progress(progress, phase="data", completed=total, total=total, hits=0, current_code="", current_name="")
        _log(log, f"数据阶段完成：可分析 {len(histories)}，跳过 {skipped}")
        return histories

    def _analyze_histories(self, stocks, histories, config, log=None, progress=None, stop_event=None) -> list[dict]:
        passed: list[dict] = []
        all_scored: list[dict] = []
        analyzable = [stock for stock in stocks if stock.code in histories]
        total = len(analyzable)
        completed = 0
        workers = max(1, min(config.max_workers, 24))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_stock = {
                executor.submit(self._analyze_one_full, stock, histories[stock.code], config, stop_event): stock
                for stock in analyzable
            }
            for future in as_completed(future_to_stock):
                if stop_event is not None and stop_event.is_set():
                    raise ScanCancelled("扫描已取消")
                completed += 1
                stock = future_to_stock[future]
                scored = future.result()
                all_scored.append(scored)
                if scored["passed"]:
                    passed.append(scored["candidate"])
                    _log(log, f"命中 {scored['candidate']['code']} {scored['candidate']['name']}，评分 {scored['candidate']['score']}")
                    if progress:
                        progress(hits_detail=scored["candidate"])
                _progress(
                    progress,
                    phase="analysis",
                    completed=completed,
                    total=total,
                    hits=len(passed),
                    current_code=stock.code,
                    current_name=stock.name,
                )
                if completed == 1:
                    _log(log, f"分析阶段：多线程并行评估（共{total}只）")
                if completed == total or completed % 100 == 0 or (completed <= 20 and completed % 20 == 0):
                    _log(log, f"分析进度 {completed}/{total} ({completed*100//total}%)，当前命中 {len(passed)}")

        candidates = passed[:]
        candidates.sort(key=lambda item: item["score"], reverse=True)
        _log(log, f"扫描完成：命中 {len(candidates)} 只")
        return candidates

    def _analyze_one_full(self, stock: StockInfo, history: list[dict], config: StrategyConfig, stop_event=None) -> dict:
        try:
            if stop_event is not None and stop_event.is_set():
                raise ScanCancelled("扫描已取消")
            enriched = enrich_history(history)
            result = evaluate_stock(stock.code, stock.name, enriched, config)
            latest = enriched[-1]
            c = _candidate(stock, latest, result.score, result.reasons if result.passed else [], result.risks)
            return {"passed": result.passed, "score": result.score, "candidate": c}
        except ScanCancelled:
            raise
        except Exception:
            return {"passed": False, "score": 0, "candidate": _candidate(stock, {"date": "", "close": 0, "pct_chg": 0, "ma5": 0, "ma10": 0, "ma20": 0, "ma30": 0, "distance_ma10_pct": 0, "volume_ratio_5": 0, "turn": 0}, 0, [], [])}


def run_baostock_scan(config: StrategyConfig, log=None, progress=None, stop_event=None) -> list[dict]:
    return StockScanner().run(config, log=log, progress=progress, stop_event=stop_event)


def ensure_history_cached(code: str, config: StrategyConfig, provider=None) -> list[dict]:
    cached = db.get_cached_bars(code, config.lookback_days)
    if provider is None:
        raise RuntimeError("数据源不可用，无法获取实时K线数据")

    end = date.today()
    latest = db.latest_cached_bar_date(code)
    if latest:
        latest_date = _date_from_iso(latest)
        start = latest_date - timedelta(days=2)
    else:
        start = end - timedelta(days=max(config.lookback_days * 2, 260))

    try:
        rows = provider.get_history_range(code, start.isoformat(), end.isoformat())
        db.upsert_bars(code, rows)
    except Exception:
        pass
    return db.get_cached_bars(code, config.lookback_days)


def _date_from_iso(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _excluded_by_code_or_name(stock: StockInfo, config: StrategyConfig) -> bool:
    name = stock.name.upper()
    code = stock.code.lower()
    number = code.split(".")[-1]
    if config.exclude_st and ("ST" in name or "退" in stock.name):
        return True
    if config.exclude_bj and (code.startswith("bj.") or number.startswith("8") or number.startswith("4")):
        return True
    if config.exclude_star and (number.startswith("688") or number.startswith("689")):
        return True
    if config.exclude_chinext and (number.startswith("300") or number.startswith("301")):
        return True
    if config.exclude_etf and _is_etf_or_index(code, name, number):
        return True
    return False


def _is_etf_or_index(code: str, name: str, number: str) -> bool:
    if code.startswith("bj."):
        return False
    if "ETF" in name or "LOF" in name:
        return True
    if number.startswith(("51", "56", "588", "589", "159")):
        return True
    if number.startswith(("399", "899")):
        return True
    if number.startswith("000") and code.startswith("sh."):
        return True
    if "指" in name and "数" in name:
        return True
    return False


def _log(log, message: str) -> None:
    if log is not None:
        log(message)


def _progress(progress, **values) -> None:
    if progress is not None:
        progress(**values)


def _candidate(stock: StockInfo, latest: dict, score: int, reasons: list[str], risks: list[str]) -> dict:
    return {
        "code": stock.code,
        "name": stock.name,
        "trade_date": latest["date"],
        "close": latest["close"],
        "pct_chg": latest["pct_chg"],
        "ma5": latest["ma5"],
        "ma10": latest["ma10"],
        "ma20": latest["ma20"],
        "ma30": latest["ma30"],
        "distance_ma10_pct": latest["distance_ma10_pct"],
        "volume_ratio_5": latest["volume_ratio_5"],
        "turn": latest.get("turn", 0),
        "score": score,
        "reasons": reasons,
        "risks": risks,
    }
