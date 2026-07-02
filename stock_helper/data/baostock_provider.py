from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(slots=True)
class StockInfo:
    code: str
    name: str


class BaoStockProvider:
    def __init__(self) -> None:
        import baostock as bs

        self.bs = bs

    def __enter__(self) -> "BaoStockProvider":
        result = self.bs.login()
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock登录失败: {result.error_msg}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.bs.logout()

    def list_stocks(self) -> list[StockInfo]:
        today = date.today().isoformat()
        result = self.bs.query_all_stock(day=today)
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
        result = self.bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,turn",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            frequency="d",
            adjustflag="2",
        )
        rows = _result_rows(result)
        return rows[-lookback_days:]


def _result_rows(result) -> list[dict]:
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    rows = []
    fields = result.fields
    while result.next():
        values = result.get_row_data()
        rows.append(dict(zip(fields, values, strict=False)))
    return rows
