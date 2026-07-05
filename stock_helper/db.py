import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from stock_helper.config import StrategyConfig


DEFAULT_DB_PATH = Path("data/stock_helper.db")


def get_db_path() -> Path:
    return Path(os.getenv("STOCK_HELPER_DB", DEFAULT_DB_PATH))


@contextmanager
def connect(db_path: Path | None = None):
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at TEXT NOT NULL,
                params_json TEXT NOT NULL,
                hit_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                finished_at TEXT,
                duration_seconds REAL
            );

            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL NOT NULL,
                pct_chg REAL NOT NULL,
                ma5 REAL,
                ma10 REAL,
                ma20 REAL,
                ma30 REAL,
                distance_ma10_pct REAL,
                distance_ma20_pct REAL,
                volume_ratio_5 REAL,
                recent_big_yang INTEGER NOT NULL DEFAULT 0,
                recent_near_limit_up INTEGER NOT NULL DEFAULT 0,
                trend_status TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                turn REAL,
                score INTEGER NOT NULL,
                reasons TEXT NOT NULL,
                risks TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES scan_tasks(id)
            );

            CREATE TABLE IF NOT EXISTS stock_daily_bars (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                turn REAL NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (code, trade_date)
            );

            CREATE INDEX IF NOT EXISTS idx_stock_daily_bars_code_date
            ON stock_daily_bars (code, trade_date);

            CREATE TABLE IF NOT EXISTS stock_info_cache (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS trade_calendar (
                trade_date TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS daily_kline (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL NOT NULL,
                pct_chg REAL NOT NULL,
                turnover REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (code, trade_date)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_kline_code_date
            ON daily_kline (code, trade_date);
            """
        )
        daily_columns = {row["name"] for row in conn.execute("PRAGMA table_info(daily_kline)").fetchall()}
        if "turnover" not in daily_columns:
            conn.execute("ALTER TABLE daily_kline ADD COLUMN turnover REAL NOT NULL DEFAULT 0")
        candidate_columns = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)").fetchall()}
        candidate_additions = {
            "distance_ma20_pct": "REAL",
            "recent_big_yang": "INTEGER NOT NULL DEFAULT 0",
            "recent_near_limit_up": "INTEGER NOT NULL DEFAULT 0",
            "trend_status": "TEXT NOT NULL DEFAULT ''",
            "reason": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in candidate_additions.items():
            if name not in candidate_columns:
                conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} {definition}")
        scan_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scan_tasks)").fetchall()}
        if "finished_at" not in scan_columns:
            conn.execute("ALTER TABLE scan_tasks ADD COLUMN finished_at TEXT")
        if "duration_seconds" not in scan_columns:
            conn.execute("ALTER TABLE scan_tasks ADD COLUMN duration_seconds REAL")


def create_scan(config: StrategyConfig, status: str = "running", error_message: str | None = None) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scan_tasks (scanned_at, params_json, hit_count, status, error_message)
            VALUES (datetime('now', 'localtime'), ?, 0, ?, ?)
            """,
            (json.dumps(config.to_dict(), ensure_ascii=False), status, error_message),
        )
        return int(cursor.lastrowid)


def finish_scan(scan_id: int, hit_count: int, status: str = "success", error_message: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE scan_tasks
            SET hit_count = ?, status = ?, error_message = ?,
                finished_at = datetime('now', 'localtime'),
                duration_seconds = ROUND((julianday(datetime('now', 'localtime')) - julianday(scanned_at)) * 86400, 3)
            WHERE id = ?
            """,
            (hit_count, status, error_message, scan_id),
        )


def fail_running_scans(message: str = "服务重启，原扫描任务已中断") -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE scan_tasks
            SET status = 'failed', error_message = ?, finished_at = datetime('now', 'localtime'),
                duration_seconds = ROUND((julianday(datetime('now', 'localtime')) - julianday(scanned_at)) * 86400, 3)
            WHERE status = 'running'
            """,
            (message,),
        )
        return cursor.rowcount


def health_check() -> bool:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'scan_tasks'"
            ).fetchone()
        return bool(row and row["count"] == 1)
    except sqlite3.Error:
        return False


def replace_candidates(scan_id: int, candidates: list[dict]) -> None:
    with connect() as conn:
        _replace_candidates(conn, scan_id, candidates)


def complete_scan(scan_id: int, candidates: list[dict]) -> None:
    """Persist results and terminal success state in one transaction."""
    with connect() as conn:
        _replace_candidates(conn, scan_id, candidates)
        conn.execute(
            """
            UPDATE scan_tasks
            SET hit_count = ?, status = 'success', error_message = NULL,
                finished_at = datetime('now', 'localtime'),
                duration_seconds = ROUND((julianday(datetime('now', 'localtime')) - julianday(scanned_at)) * 86400, 3)
            WHERE id = ?
            """,
            (len(candidates), scan_id),
        )


def clear_all() -> None:
    with connect() as conn:
        for table in ("candidates", "stock_daily_bars", "daily_kline", "stock_info_cache", "trade_calendar", "scan_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")


def _replace_candidates(conn: sqlite3.Connection, scan_id: int, candidates: list[dict]) -> None:
    conn.execute("DELETE FROM candidates WHERE scan_id = ?", (scan_id,))
    conn.executemany(
            """
            INSERT INTO candidates (
                scan_id, code, name, trade_date, close, pct_chg, ma5, ma10, ma20, ma30,
                distance_ma10_pct, distance_ma20_pct, volume_ratio_5, turn,
                recent_big_yang, recent_near_limit_up, trend_status, reason,
                score, reasons, risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    scan_id,
                    item["code"],
                    item["name"],
                    item["trade_date"],
                    item["close"],
                    item["pct_chg"],
                    item["ma5"],
                    item["ma10"],
                    item["ma20"],
                    item["ma30"],
                    item["distance_ma10_pct"],
                    item.get("distance_ma20_pct", item.get("distance_to_ma20")),
                    item["volume_ratio_5"],
                    item["turn"],
                    int(bool(item.get("recent_big_yang"))),
                    int(bool(item.get("recent_near_limit_up"))),
                    item.get("trend_status", ""),
                    item.get("reason", "，".join(item.get("reasons", []))),
                    item["score"],
                    json.dumps(item["reasons"], ensure_ascii=False),
                    json.dumps(item["risks"], ensure_ascii=False),
                )
                for item in candidates
            ],
    )


def latest_scan() -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            """
            SELECT *,
                CASE WHEN status = 'running'
                     THEN ROUND((julianday(datetime('now', 'localtime')) - julianday(scanned_at)) * 86400, 3)
                     ELSE duration_seconds END AS elapsed_seconds
            FROM scan_tasks ORDER BY id DESC LIMIT 1
            """
        ).fetchone()


def latest_candidates(scan_id: int | None = None, trade_date: str | None = None) -> list[dict]:
    if scan_id is None:
        scan = latest_scan()
        scan_id = scan["id"] if scan else None
    if scan_id is None:
        return []
    return scan_candidates(scan_id, trade_date=trade_date)


def scan_candidates(scan_id: int, trade_date: str | None = None) -> list[dict]:
    with connect() as conn:
        if trade_date is None:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE scan_id = ? ORDER BY score DESC, close ASC",
                (scan_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE scan_id = ? AND trade_date = ? ORDER BY score DESC, close ASC",
                (scan_id, trade_date),
            ).fetchall()
    return [_decode_candidate(row) for row in rows]


def latest_data_date() -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT MAX(trade_date) AS d FROM stock_daily_bars").fetchone()
    return row["d"] if row else None


def latest_summary(candidate_date: str | None = None) -> dict:
    scan = latest_scan()
    candidates = latest_candidates(scan["id"], trade_date=candidate_date) if scan else []
    params = json.loads(scan["params_json"]) if scan else {}
    return {
        "scan": scan,
        "params": params,
        "count": len(candidates),
        "top": candidates[0] if candidates else None,
        "data_date": latest_data_date(),
    }


def get_cached_bars(code: str, limit: int) -> list[dict]:
    bars, _ = get_cached_bars_state(code, limit)
    return bars


def get_cached_bars_state(code: str, limit: int) -> tuple[list[dict], str | None]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date AS date, open, high, low, close, volume, turn, updated_at
            FROM stock_daily_bars
            WHERE code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (code, limit),
        ).fetchall()
    updated_at = max((row["updated_at"] for row in rows), default=None)
    bars = []
    for row in reversed(rows):
        item = dict(row)
        item.pop("updated_at", None)
        bars.append(item)
    return bars, updated_at


def latest_cached_bar_date(code: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(trade_date) AS latest_date FROM stock_daily_bars WHERE code = ?",
            (code,),
        ).fetchone()
    return row["latest_date"] if row and row["latest_date"] else None


def upsert_bars(code: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO stock_daily_bars (code, trade_date, open, high, low, close, volume, turn, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(code, trade_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                turn = excluded.turn,
                updated_at = datetime('now', 'localtime')
            """,
            [
                (
                    code,
                    row["date"],
                    _to_float(row.get("open")),
                    _to_float(row.get("high")),
                    _to_float(row.get("low")),
                    _to_float(row.get("close")),
                    _to_float(row.get("volume") or row.get("vol")),
                    _to_float(row.get("turn")),
                )
                for row in rows
                if row.get("date")
            ],
        )
    return len(rows)


def upsert_stock_list(stocks) -> int:
    if not stocks:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO stock_info_cache (code, name, updated_at)
            VALUES (?, ?, datetime('now', 'localtime'))
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                updated_at = datetime('now', 'localtime')
            """,
            [(stock.code, stock.name) for stock in stocks],
        )
    return len(stocks)


def cached_stock_list() -> list:
    stocks, _ = cached_stock_list_state()
    return stocks


def cached_stock_list_state() -> tuple[list, str | None]:
    from stock_helper.data import StockInfo

    with connect() as conn:
        rows = conn.execute("SELECT code, name, updated_at FROM stock_info_cache ORDER BY code").fetchall()
    if rows:
        updated_at = max(row["updated_at"] for row in rows)
        return [StockInfo(code=row["code"], name=row["name"]) for row in rows], updated_at

    # fallback: 从 stock_daily_bars 中提取已有代码
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT code FROM stock_daily_bars ORDER BY code"
        ).fetchall()
    return [StockInfo(code=row["code"], name=row["code"]) for row in rows], None


def replace_trade_calendar(trade_dates: list[str]) -> None:
    if not trade_dates:
        return
    with connect() as conn:
        conn.execute("DELETE FROM trade_calendar")
        conn.executemany(
            "INSERT INTO trade_calendar (trade_date, updated_at) VALUES (?, datetime('now', 'localtime'))",
            [(value,) for value in sorted(set(trade_dates))],
        )


def cached_trade_calendar() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT trade_date FROM trade_calendar ORDER BY trade_date").fetchall()
    return [row["trade_date"] for row in rows]


def market_data_stocks() -> list[dict]:
    """List stocks seen by either the viewer cache or the scan cache."""
    with connect() as conn:
        rows = conn.execute(
            """
            WITH cached_dates AS (
                SELECT code, trade_date FROM daily_kline
                UNION
                SELECT code, trade_date FROM stock_daily_bars
            ), summary AS (
                SELECT code, MAX(trade_date) AS latest_trade_date, COUNT(*) AS rows_count
                FROM cached_dates GROUP BY code
            )
            SELECT
                summary.code,
                COALESCE(NULLIF(info.name, ''), summary.code) AS name,
                summary.latest_trade_date,
                summary.rows_count,
                COALESCE(
                    (SELECT source FROM daily_kline k
                     WHERE k.code = summary.code AND k.trade_date = summary.latest_trade_date LIMIT 1),
                    'scan_cache'
                ) AS source
            FROM summary
            LEFT JOIN stock_info_cache info ON info.code = summary.code
            """
        ).fetchall()
    result = [dict(row) for row in rows]
    result.sort(key=lambda item: (item["code"].split(".")[-1], item["code"]))
    return result


def market_cached_daily_kline(code: str, limit: int) -> list[dict]:
    """Read and merge both local K-line caches without network access."""
    with connect() as conn:
        scan_rows = conn.execute(
            """
            SELECT code, trade_date, open, high, low, close, volume,
                   0.0 AS amount, NULL AS pct_chg, turn AS turnover,
                   'scan_cache' AS source, updated_at
            FROM stock_daily_bars WHERE code = ?
            ORDER BY trade_date DESC LIMIT ?
            """,
            (code, limit),
        ).fetchall()
        daily_rows = conn.execute(
            """
            SELECT code, trade_date, open, high, low, close, volume,
                   amount, pct_chg, turnover, source, updated_at
            FROM daily_kline WHERE code = ?
            ORDER BY trade_date DESC LIMIT ?
            """,
            (code, limit),
        ).fetchall()
    # Scan cache is loaded first; richer daily_kline rows win on date conflicts.
    merged = {row["trade_date"]: dict(row) for row in scan_rows}
    merged.update({row["trade_date"]: dict(row) for row in daily_rows})
    result = sorted(merged.values(), key=lambda row: row["trade_date"])[-limit:]
    previous_close = None
    for row in result:
        if row["pct_chg"] is None:
            row["pct_chg"] = ((row["close"] / previous_close) - 1) * 100 if previous_close else 0.0
        previous_close = row["close"]
    return result


def _decode_candidate(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["reasons"] = json.loads(item["reasons"])
    item["risks"] = json.loads(item["risks"])
    item["volume_ratio"] = item.get("volume_ratio_5")
    item["distance_to_ma10"] = item.get("distance_ma10_pct")
    item["distance_to_ma20"] = item.get("distance_ma20_pct")
    item["recent_big_yang"] = bool(item.get("recent_big_yang"))
    item["recent_near_limit_up"] = bool(item.get("recent_near_limit_up"))
    return item


def _to_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
