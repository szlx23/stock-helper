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
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at TEXT NOT NULL,
                params_json TEXT NOT NULL,
                hit_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT
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
                volume_ratio_5 REAL,
                turn REAL,
                score INTEGER NOT NULL,
                reasons TEXT NOT NULL,
                risks TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES scan_tasks(id)
            );
            """
        )


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
            "UPDATE scan_tasks SET hit_count = ?, status = ?, error_message = ? WHERE id = ?",
            (hit_count, status, error_message, scan_id),
        )


def replace_candidates(scan_id: int, candidates: list[dict]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM candidates WHERE scan_id = ?", (scan_id,))
        conn.executemany(
            """
            INSERT INTO candidates (
                scan_id, code, name, trade_date, close, pct_chg, ma5, ma10, ma20, ma30,
                distance_ma10_pct, volume_ratio_5, turn, score, reasons, risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    item["volume_ratio_5"],
                    item["turn"],
                    item["score"],
                    json.dumps(item["reasons"], ensure_ascii=False),
                    json.dumps(item["risks"], ensure_ascii=False),
                )
                for item in candidates
            ],
        )


def latest_scan() -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM scan_tasks ORDER BY id DESC LIMIT 1").fetchone()


def latest_candidates() -> list[dict]:
    scan = latest_scan()
    if not scan:
        return []
    return scan_candidates(scan["id"])


def scan_candidates(scan_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE scan_id = ? ORDER BY score DESC, close ASC",
            (scan_id,),
        ).fetchall()
    return [_decode_candidate(row) for row in rows]


def latest_summary() -> dict:
    scan = latest_scan()
    candidates = latest_candidates() if scan else []
    params = json.loads(scan["params_json"]) if scan else {}
    return {
        "scan": scan,
        "params": params,
        "count": len(candidates),
        "top": candidates[0] if candidates else None,
    }


def _decode_candidate(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["reasons"] = json.loads(item["reasons"])
    item["risks"] = json.loads(item["risks"])
    return item
