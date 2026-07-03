"""Multi-source data provider with automatic fallback."""

from stock_helper.data import StockInfo


class MultiProvider:
    """Try providers in priority order; on failure, fall through to the next."""

    def __init__(self, providers: list, log=None) -> None:
        self._providers = providers
        self._log = log
        self._active = None
        self._active_index = -1
        self._source_name = "unknown"

    @property
    def source_name(self) -> str:
        return self._source_name

    def __enter__(self) -> "MultiProvider":
        self._activate_from(0)
        return self

    def _activate_from(self, start_index: int) -> None:
        last_error = None
        for index in range(start_index, len(self._providers)):
            provider_class = self._providers[index]
            name = getattr(provider_class, "SOURCE_NAME", provider_class.__name__)
            instance = None
            try:
                instance = provider_class()
                instance.__enter__()
                self._active = instance
                self._active_index = index
                self._source_name = name
                self._emit_log(f"使用数据源：{name}")
                return
            except Exception as exc:
                last_error = exc
                self._emit_log(f"数据源 {name} 不可用：{exc}")
                try:
                    if instance:
                        instance.__exit__(None, None, None)
                except Exception:
                    pass
                self._active = None
                self._active_index = -1
        raise RuntimeError(f"所有数据源均不可用，最后错误：{last_error}")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._active:
            try:
                self._active.__exit__(exc_type, exc, tb)
            except Exception:
                pass
        self._active = None
        self._active_index = -1
        return None

    def list_stocks(self) -> list[StockInfo]:
        return self._call_with_fallback("list_stocks")

    def get_history(self, code: str, lookback_days: int) -> list[dict]:
        return self._call_with_fallback("get_history", code, lookback_days)

    def get_history_range(self, code: str, start_date: str, end_date: str) -> list[dict]:
        return self._call_with_fallback("get_history_range", code, start_date, end_date)

    def _call_with_fallback(self, method: str, *args):
        if self._active is None:
            raise RuntimeError("数据源尚未连接")
        try:
            return getattr(self._active, method)(*args)
        except Exception as exc:
            failed_name = self._source_name
            next_index = self._active_index + 1
            self._emit_log(f"数据源 {failed_name} 请求失败：{exc}，尝试后备源")
            try:
                self._active.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
            self._active = None
            self._active_index = -1
            self._activate_from(next_index)
            return getattr(self._active, method)(*args)

    def _emit_log(self, msg: str) -> None:
        if self._log:
            self._log(msg)


def make_multi_provider(log=None):
    """Build MultiProvider with available sources in priority order."""
    providers = []
    from stock_helper.data.tx_provider import TXProvider
    providers.append(TXProvider)
    from stock_helper.data.sina_provider import SinaProvider
    providers.append(SinaProvider)
    return MultiProvider(providers, log=log)
