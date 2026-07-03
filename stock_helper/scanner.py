from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime, timedelta
import threading
from zoneinfo import ZoneInfo

from stock_helper import db
from stock_helper.config import StrategyConfig
from stock_helper.data import StockInfo
from stock_helper.data.multi_provider import make_multi_provider

from stock_helper.indicators import enrich_history
from stock_helper.strategy import evaluate_stock


class ScanCancelled(Exception):
    pass


class RealtimeDataUnavailable(RuntimeError):
    pass


class _ThreadLocalProviderPool:
    """One provider instance per fetch worker; providers are never shared across threads."""

    def __init__(self, provider_factory) -> None:
        self._provider_factory = provider_factory
        self._local = threading.local()
        self._instances = []
        self._lock = threading.Lock()

    def get(self):
        provider = getattr(self._local, "provider", None)
        if provider is None:
            provider = self._provider_factory(log=None)
            provider.__enter__()
            self._local.provider = provider
            with self._lock:
                self._instances.append(provider)
        return provider

    def close(self) -> None:
        with self._lock:
            instances, self._instances = self._instances, []
        for provider in instances:
            try:
                provider.__exit__(None, None, None)
            except Exception:
                continue


class StockScanner:
    def __init__(self, provider_factory=make_multi_provider) -> None:
        self._provider_factory = provider_factory

    def run(self, config: StrategyConfig, log=None, progress=None, stop_event=None) -> list[dict]:
        config.validate()
        stocks, source = self._load_stock_universe(config, log)
        stocks = [stock for stock in stocks if not _excluded_by_code_or_name(stock, config)]
        _log(log, f"读取并预过滤股票：{len(stocks)} 只")
        if len(stocks) > config.max_scan_count:
            stocks = _limit_stocks(stocks, config.max_scan_count)
            _log(log, f"按代码稳定截取前 {config.max_scan_count} 只进行分析")

        _log(log, f"流水线启动：{config.fetch_workers} 路拉取，{config.max_workers} 路分析（列表：{source}）")
        _log(log, f"实时硬门槛：仅分析本轮成功拉取且包含 {_market_today().isoformat()} 有效日线的股票")
        _log(log, f"K线策略：保留 {config.lookback_days} 日，增量补缺至当日")
        return self._run_pipeline(stocks, config, log, progress, stop_event)

    def _load_stock_universe(self, config: StrategyConfig, log=None) -> tuple[list[StockInfo], str]:
        cached, updated_at = db.cached_stock_list_state()
        if cached and _cache_is_fresh(updated_at, config.stock_list_ttl_minutes):
            _log(log, f"使用本地股票列表缓存：{len(cached)} 只")
            return cached, "本地缓存"

        provider = None
        try:
            provider = self._provider_factory(log=log)
            opened = provider.__enter__()
            stocks = opened.list_stocks()
            source = provider.source_name
            if not stocks:
                _log(log, f"{source} 返回空列表，1秒后重试...")
                import time
                time.sleep(1)
                stocks = opened.list_stocks()
            if not stocks:
                raise RuntimeError(f"{source} 返回空股票列表")
            db.upsert_stock_list(stocks)
            _log(log, f"股票列表已更新（{source}）：{len(stocks)} 只")
            return stocks, source
        except Exception as exc:
            if cached:
                _log(log, f"股票列表更新失败，使用本地缓存：{exc}")
                return cached, "本地缓存（更新失败）"
            raise RuntimeError(f"股票列表不可用：{exc}") from exc
        finally:
            if provider is not None:
                provider.__exit__(None, None, None)

    def _run_pipeline(self, stocks, config, log=None, progress=None, stop_event=None) -> list[dict]:
        total = len(stocks)
        if total == 0:
            _log(log, "扫描完成：预过滤后没有需要分析的股票")
            return []

        fetch_workers = max(1, min(config.fetch_workers, 8))
        analysis_workers = max(1, min(config.max_workers, 24))
        provider_pool = _ThreadLocalProviderPool(self._provider_factory)
        fetch_executor = ThreadPoolExecutor(max_workers=fetch_workers, thread_name_prefix="stock-fetch")
        analysis_executor = ThreadPoolExecutor(max_workers=analysis_workers, thread_name_prefix="stock-analysis")
        stock_iter = iter(stocks)
        fetch_pending: dict[Future, StockInfo] = {}
        analysis_pending: dict[Future, StockInfo] = {}
        passed: list[dict] = []
        fetched = analyzed = skipped = realtime_skipped = fetch_fails = analysis_failures = analyzable = 0

        def submit_fetches() -> None:
            while len(fetch_pending) < fetch_workers * 2:
                stock = next(stock_iter, None)
                if stock is None:
                    break
                future = fetch_executor.submit(self._fetch_one, stock, config, provider_pool, stop_event)
                fetch_pending[future] = stock

        def emit_progress(stock: StockInfo | None = None, action: str = "") -> None:
            _progress(
                progress,
                phase="pipeline",
                completed=analyzed + skipped,
                total=total,
                fetched=fetched,
                analyzed=analyzed,
                skipped=skipped,
                realtime_skipped=realtime_skipped,
                hits=len(passed),
                current_code=stock.code if stock else "",
                current_name=stock.name if stock else "",
                current_action=action,
            )

        submit_fetches()
        emit_progress()
        try:
            while fetch_pending or analysis_pending:
                if stop_event is not None and stop_event.is_set():
                    raise ScanCancelled("扫描已取消")
                done, _ = wait((*fetch_pending.keys(), *analysis_pending.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    if future in fetch_pending:
                        stock = fetch_pending.pop(future)
                        fetched += 1
                        try:
                            history, bars_before, realtime_verified = future.result()
                        except ScanCancelled:
                            raise
                        except Exception:
                            history, bars_before, realtime_verified = [], 0, False
                        if not realtime_verified:
                            realtime_skipped += 1
                        if realtime_verified and len(history) >= 35:
                            analyzable += 1
                            analysis_future = analysis_executor.submit(
                                self._analyze_one_full, stock, history, config, stop_event
                            )
                            analysis_pending[analysis_future] = stock
                        else:
                            skipped += 1
                            if bars_before < 35:
                                fetch_fails += 1
                        emit_progress(stock, "fetch")
                        submit_fetches()
                        if fetched == total or fetched % 50 == 0 or (fetched <= 10 and fetched % 5 == 0):
                            _log(log, f"流水线拉取 {fetched}/{total}，已分析 {analyzed}，非实时 {realtime_skipped}，跳过 {skipped}，命中 {len(passed)}")
                    else:
                        stock = analysis_pending.pop(future)
                        analyzed += 1
                        scored = future.result()
                        if scored.get("error"):
                            analysis_failures += 1
                        if scored["passed"]:
                            passed.append(scored["candidate"])
                            _log(log, f"命中 {scored['candidate']['code']} {scored['candidate']['name']}，评分 {scored['candidate']['score']}")
                            if progress:
                                progress(hits_detail=scored["candidate"])
                        emit_progress(stock, "analysis")
                        if analyzed == 1:
                            _log(log, "首只股票已完成分析，后续行情仍在并行拉取")
                        if analyzed % 100 == 0:
                            _log(log, f"流水线分析 {analyzed}/{analyzable}，拉取 {fetched}/{total}，命中 {len(passed)}")
        finally:
            for future in (*fetch_pending.keys(), *analysis_pending.keys()):
                future.cancel()
            fetch_executor.shutdown(wait=True, cancel_futures=True)
            analysis_executor.shutdown(wait=True, cancel_futures=True)
            provider_pool.close()

        if analyzable == 0:
            raise RuntimeError("没有股票取得本轮当日实时行情，已全部停止分析")
        if analysis_failures == analyzable:
            raise RuntimeError("所有股票分析均失败，请检查行情数据格式")
        if fetch_fails:
            _log(log, f"数据提示：{fetch_fails} 只股票未取得足够历史行情")
        if realtime_skipped:
            _log(log, f"实时门槛：{realtime_skipped} 只股票未取得本轮当日行情，未参与分析")
        if analysis_failures:
            _log(log, f"分析警告：{analysis_failures} 只股票因数据异常未完成评估")
        passed.sort(key=lambda item: (-item["score"], item["code"]))
        emit_progress()
        _log(log, f"扫描完成：拉取 {fetched}，分析 {analyzed}，命中 {len(passed)} 只")
        return passed

    @staticmethod
    def _fetch_one(stock, config, provider_pool, stop_event=None):
        if stop_event is not None and stop_event.is_set():
            raise ScanCancelled("扫描已取消")
        cached, _ = db.get_cached_bars_state(stock.code, config.lookback_days)
        bars_before = len(cached)
        try:
            history = ensure_history_cached(
                stock.code,
                config,
                provider_pool.get(),
                cached=cached,
            )
        except RealtimeDataUnavailable:
            return [], bars_before, False
        return history, bars_before, True

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
        if total > 0 and not histories:
            raise RuntimeError("没有可分析的行情数据，请检查数据源或稍后重试")
        return histories

    def _analyze_histories(self, stocks, histories, config, log=None, progress=None, stop_event=None) -> list[dict]:
        passed: list[dict] = []
        analyzable = [stock for stock in stocks if stock.code in histories]
        total = len(analyzable)
        completed = 0
        analysis_failures = 0
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
                if scored.get("error"):
                    analysis_failures += 1
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

        if total > 0 and analysis_failures == total:
            raise RuntimeError("所有股票分析均失败，请检查行情数据格式")
        if analysis_failures:
            _log(log, f"分析警告：{analysis_failures} 只股票因数据异常未完成评估")
        candidates = passed[:]
        candidates.sort(key=lambda item: item["score"], reverse=True)
        _log(log, f"扫描完成：命中 {len(candidates)} 只")
        return candidates

    def _analyze_one_full(self, stock: StockInfo, history: list[dict], config: StrategyConfig, stop_event=None) -> dict:
        try:
            if stop_event is not None and stop_event.is_set():
                raise ScanCancelled("扫描已取消")
            if not history or history[-1].get("date") != _market_today().isoformat():
                raise RealtimeDataUnavailable(f"{stock.code} 分析前实时日期校验失败")
            enriched = enrich_history(history)
            if not enriched or enriched[-1].get("date") != _market_today().isoformat():
                raise RealtimeDataUnavailable(f"{stock.code} 当日行情无有效价格")
            result = evaluate_stock(stock.code, stock.name, enriched, config)
            latest = enriched[-1]
            c = _candidate(stock, latest, result.score, result.reasons if result.passed else [], result.risks)
            return {"passed": result.passed, "score": result.score, "candidate": c, "error": None}
        except ScanCancelled:
            raise
        except Exception as exc:
            return {"passed": False, "score": 0, "candidate": _candidate(stock, {"date": "", "close": 0, "pct_chg": 0, "ma5": 0, "ma10": 0, "ma20": 0, "ma30": 0, "distance_ma10_pct": 0, "volume_ratio_5": 0, "turn": 0}, 0, [], []), "error": str(exc)}


def run_baostock_scan(config: StrategyConfig, log=None, progress=None, stop_event=None) -> list[dict]:
    return StockScanner().run(config, log=log, progress=progress, stop_event=stop_event)


def ensure_history_cached(
    code: str,
    config: StrategyConfig,
    provider=None,
    *,
    cached: list[dict] | None = None,
) -> list[dict]:
    if cached is None:
        cached, _ = db.get_cached_bars_state(code, config.lookback_days)
    if provider is None:
        raise RuntimeError("数据源不可用，无法获取实时K线数据")

    end = _market_today()
    latest = cached[-1]["date"] if cached else None
    if latest:
        latest_date = _date_from_iso(latest)
        start = latest_date - timedelta(days=config.cache_refresh_days)
    else:
        start = end - timedelta(days=max(config.lookback_days * 2, 260))

    try:
        rows = provider.get_history_range(code, start.isoformat(), end.isoformat())
    except Exception as exc:
        raise RealtimeDataUnavailable(f"{code} 本轮行情请求失败：{exc}") from exc
    today = _market_today().isoformat()
    if not any(_is_valid_current_row(row, today) for row in rows):
        raise RealtimeDataUnavailable(f"{code} 行情未更新到 {today} 或当日价格无效")
    rows_to_store = [
        row for row in rows
        if str(row.get("date", ""))[:10] != today or _is_valid_current_row(row, today)
    ]
    db.upsert_bars(code, rows_to_store)
    history = db.get_cached_bars(code, config.lookback_days)
    if not history or history[-1]["date"] != today:
        raise RealtimeDataUnavailable(f"{code} 当日行情持久化校验失败")
    return history


def _cache_is_fresh(updated_at: str | None, ttl_minutes: int) -> bool:
    if not updated_at or ttl_minutes <= 0:
        return False
    try:
        age = datetime.now() - datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    return age.total_seconds() <= ttl_minutes * 60


def _market_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _is_valid_current_row(row: dict, today: str) -> bool:
    if str(row.get("date", ""))[:10] != today:
        return False
    for field in ("open", "high", "low", "close"):
        try:
            if float(row.get(field, 0)) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


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


def _limit_stocks(stocks: list[StockInfo], limit: int) -> list[StockInfo]:
    return sorted(stocks, key=lambda stock: stock.code)[:limit]


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
