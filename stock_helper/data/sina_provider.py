"""Sina data provider via AKShare."""

from datetime import date, timedelta

from stock_helper.data import StockInfo, normalize_a_share_code


class SinaProvider:
    """Provider using AKShare with Sina source."""

    SOURCE_NAME = "AKShare(Sina)"

    def __init__(self) -> None:
        pass

    def __enter__(self) -> "SinaProvider":
        import os
        os.environ.pop("http_proxy", None)
        os.environ.pop("https_proxy", None)
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("all_proxy", None)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def list_stocks(self) -> list[StockInfo]:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            code = normalize_a_share_code(str(row["code"]))
            if code:
                stocks.append(StockInfo(code=code, name=str(row["name"])))
        return stocks

    def get_history(self, code: str, lookback_days: int) -> list[dict]:
        end = date.today()
        start = end - timedelta(days=max(lookback_days * 2, 260))
        return self.get_history_range(code, start.isoformat(), end.isoformat())[-lookback_days:]

    def get_history_range(self, code: str, start_date: str, end_date: str) -> list[dict]:
        import akshare as ak
        symbol = code.replace(".", "")
        df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq")
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "date": str(row["date"]),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("volume")),
                "turn": _f(row.get("turnover") or row.get("turn", 0)),
            })
        return rows


def _f(val) -> float:
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
