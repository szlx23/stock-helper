"""Multi-source data provider with automatic fallback."""

from stock_helper.data import StockInfo


class MultiProvider:
    """Try providers in priority order; on failure, fall through to the next."""

    def __init__(self, providers: list, log=None) -> None:
        self._providers = providers
        self._log = log
        self._active = None
        self._source_name = "unknown"

    @property
    def source_name(self) -> str:
        return self._source_name

    def __enter__(self) -> "MultiProvider":
        last_error = None
        for provider_class in self._providers:
            name = getattr(provider_class, "SOURCE_NAME", provider_class.__name__)
            try:
                instance = provider_class()
                instance.__enter__()
                self._active = instance
                self._source_name = name
                self._emit_log(f"使用数据源：{name}")
                return self
            except Exception as exc:
                last_error = exc
                self._emit_log(f"数据源 {name} 不可用：{exc}")
                try:
                    if self._active:
                        self._active.__exit__(None, None, None)
                except Exception:
                    pass
                self._active = None
        raise RuntimeError(f"所有数据源均不可用，最后错误：{last_error}")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._active:
            try:
                self._active.__exit__(exc_type, exc, tb)
            except Exception:
                pass
        self._active = None
        return None

    def list_stocks(self) -> list[StockInfo]:
        return self._active.list_stocks()

    def get_history(self, code: str, lookback_days: int) -> list[dict]:
        return self._active.get_history(code, lookback_days)

    def get_history_range(self, code: str, start_date: str, end_date: str) -> list[dict]:
        return self._active.get_history_range(code, start_date, end_date)

    def _emit_log(self, msg: str) -> None:
        if self._log:
            self._log(msg)


def make_multi_provider(log=None):
    """Build MultiProvider with available sources in priority order."""
    providers = []
    # Sina first (full OHLCV data)
    from stock_helper.data.sina_provider import SinaProvider
    providers.append(SinaProvider)
    # Tencent as fallback
    from stock_helper.data.tx_provider import TXProvider
    providers.append(TXProvider)
    return MultiProvider(providers, log=log)
