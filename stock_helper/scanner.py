from stock_helper.config import StrategyConfig
from stock_helper.data.baostock_provider import BaoStockProvider, StockInfo
from stock_helper.indicators import enrich_history
from stock_helper.strategy import evaluate_stock


class StockScanner:
    def __init__(self, provider) -> None:
        self.provider = provider

    def run(self, config: StrategyConfig) -> list[dict]:
        candidates = []
        for stock in self.provider.list_stocks():
            history = self.provider.get_history(stock.code, config.lookback_days)
            enriched = enrich_history(history)
            result = evaluate_stock(stock.code, stock.name, enriched, config)
            if result.passed:
                latest = enriched[-1]
                candidates.append(_candidate(stock, latest, result.score, result.reasons, result.risks))
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates


def run_baostock_scan(config: StrategyConfig) -> list[dict]:
    with BaoStockProvider() as provider:
        return StockScanner(provider).run(config)


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
