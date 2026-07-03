"""Bulk realtime A-share snapshot used to build the in-progress daily bar."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
import math
from zoneinfo import ZoneInfo

import requests

from stock_helper.data import normalize_a_share_code


SHANGHAI = ZoneInfo("Asia/Shanghai")


class RealtimeSnapshotError(RuntimeError):
    pass


def load_realtime_snapshot(log=None) -> dict[str, dict]:
    """Fetch the full A-share snapshot in one request and keep verifiably current rows."""
    now = datetime.now(SHANGHAI)
    url = "https://82.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "6000",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": "f2,f5,f6,f8,f12,f13,f14,f15,f16,f17,f18,f124",
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {})
        rows = list(data.get("diff", []))
        page_size = len(rows)
        total = int(data.get("total", page_size) or page_size)
        if page_size:
            page_count = math.ceil(total / page_size)

            def fetch_page(page: int):
                page_params = {**params, "pn": str(page)}
                page_response = requests.get(url, params=page_params, timeout=15)
                page_response.raise_for_status()
                return page_response.json().get("data", {}).get("diff", [])

            with ThreadPoolExecutor(max_workers=8, thread_name_prefix="realtime-page") as executor:
                futures = [executor.submit(fetch_page, page) for page in range(2, page_count + 1)]
                for future in as_completed(futures):
                    rows.extend(future.result())
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise RealtimeSnapshotError(f"实时快照请求失败：{exc}") from exc
    if not rows:
        raise RealtimeSnapshotError("实时快照返回空数据")

    snapshot = {}
    stale = invalid = 0
    for item in rows:
        code = normalize_a_share_code(str(item.get("f12", "")))
        quote_time = _quote_datetime(item.get("f124"))
        if not code or not _quote_is_current(quote_time, now):
            stale += 1
            continue
        bar = {
            "date": now.date().isoformat(),
            "open": _number(item.get("f17")),
            "high": _number(item.get("f15")),
            "low": _number(item.get("f16")),
            "close": _number(item.get("f2")),
            # Tencent history uses amount as its volume-like series; keep units consistent.
            "volume": _number(item.get("f6")),
            "turn": _number(item.get("f8")),
            "quote_time": quote_time.isoformat() if quote_time else "",
        }
        if any(bar[field] <= 0 for field in ("open", "high", "low", "close")):
            invalid += 1
            continue
        snapshot[code] = bar
    if log:
        log(f"实时快照：有效 {len(snapshot)} 只，非今日/过期 {stale}，价格无效 {invalid}")
    if not snapshot:
        raise RealtimeSnapshotError("实时快照中没有可验证的当日行情")
    return snapshot


def _quote_datetime(value) -> datetime | None:
    try:
        timestamp = int(float(value))
        if timestamp <= 0:
            return None
        return datetime.fromtimestamp(timestamp, SHANGHAI)
    except (TypeError, ValueError, OSError):
        return None


def _quote_is_current(quote_time: datetime | None, now: datetime) -> bool:
    if quote_time is None or quote_time.date() != now.date():
        return False
    current = now.time()
    in_session = time(9, 15) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)
    if not in_session:
        return True
    age_seconds = (now - quote_time).total_seconds()
    return -60 <= age_seconds <= 5 * 60


def _number(value) -> float:
    try:
        number = float(value)
        return number if number == number else 0.0
    except (TypeError, ValueError):
        return 0.0
