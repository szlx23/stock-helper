def enrich_history(rows: list[dict]) -> list[dict]:
    cleaned = [_coerce_row(row) for row in rows if _has_price(row)]
    cleaned.sort(key=lambda item: item["date"])
    for idx, row in enumerate(cleaned):
        for window in (5, 10, 20, 30):
            row[f"ma{window}"] = _moving_average(cleaned, idx, "close", window)
        row["vol_ma5"] = _moving_average(cleaned, idx, "volume", 5)
        row["pct_chg"] = _pct_change(cleaned, idx)
        row["distance_ma10_pct"] = _distance(row.get("close"), row.get("ma10"))
        row["distance_ma20_pct"] = _distance(row.get("close"), row.get("ma20"))
        row["volume_ratio_5"] = _ratio(row.get("volume"), row.get("vol_ma5"))
    return cleaned


def _coerce_row(row: dict) -> dict:
    return {
        "date": str(row.get("date", "")),
        "open": _to_float(row.get("open")),
        "high": _to_float(row.get("high")),
        "low": _to_float(row.get("low")),
        "close": _to_float(row.get("close")),
        "volume": _to_float(row.get("volume") or row.get("vol")),
        "turn": _to_float(row.get("turn")),
    }


def _has_price(row: dict) -> bool:
    return bool(row.get("date")) and _to_float(row.get("close")) > 0


def _moving_average(rows: list[dict], idx: int, key: str, window: int) -> float | None:
    if idx + 1 < window:
        return None
    values = [rows[i][key] for i in range(idx - window + 1, idx + 1)]
    if any(value is None for value in values):
        return None
    return sum(values) / window


def _pct_change(rows: list[dict], idx: int) -> float:
    if idx == 0 or rows[idx - 1]["close"] == 0:
        return 0.0
    return rows[idx]["close"] / rows[idx - 1]["close"] - 1


def _distance(value: float | None, base: float | None) -> float | None:
    if value is None or base in (None, 0):
        return None
    return value / base - 1


def _ratio(value: float | None, base: float | None) -> float | None:
    if value is None or base in (None, 0):
        return None
    return value / base


def _to_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
