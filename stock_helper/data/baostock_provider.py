from datetime import date, timedelta
from threading import RLock

from stock_helper.data import StockInfo


_BAOSTOCK_LOCK = RLock()


class BaoStockProvider:
    SOURCE_NAME = "BaoStock"

    def __init__(self) -> None:
        import baostock as bs

        self.bs = bs

    def __enter__(self) -> "BaoStockProvider":
        _BAOSTOCK_LOCK.acquire()
        result = self.bs.login()
        if result.error_code != "0":
            _BAOSTOCK_LOCK.release()
            raise RuntimeError(f"BaoStock登录失败: {result.error_msg}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.bs.logout()
        finally:
            _BAOSTOCK_LOCK.release()

    def list_stocks(self) -> list[StockInfo]:
        result = self.bs.query_all_stock()
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock query_all_stock 失败: {result.error_msg}")
        rows = _result_rows(result)
        stocks = []
        for row in rows:
            code = row.get("code", "")
            name = row.get("code_name", "") or row.get("name", "")
            if code.startswith(("sh.", "sz.", "bj.")):
                stocks.append(StockInfo(code=code, name=name))
        return stocks

    def get_history(self, code: str, lookback_days: int) -> list[dict]:
        end = date.today()
        start = end - timedelta(days=max(lookback_days * 2, 260))
        return self.get_history_range(code, start.isoformat(), end.isoformat())[-lookback_days:]

    def get_history_range(self, code: str, start_date: str, end_date: str) -> list[dict]:
        result = self.bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,turn",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2",
        )
        return _result_rows(result)


def _result_rows(result) -> list[dict]:
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    rows = []
    fields = result.fields
    while result.next():
        values = result.get_row_data()
        rows.append(dict(zip(fields, values, strict=False)))
    return rows
