from __future__ import annotations

import threading
from datetime import datetime

from stock_helper import db
from stock_helper.config import StrategyConfig
from stock_helper.scanner import ScanCancelled, run_baostock_scan


class ScanTaskManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._task_id = 0
        self._cancel_event: threading.Event | None = None
        self._logs: list[str] = []
        self._log_base_offset = 0
        self._progress: dict = {}
        self._live_hits: list[dict] = []
        self._live_offset = 0
        self._done = True
        self._outcome = "idle"

    def start(self, config: StrategyConfig) -> int:
        config.validate()
        with self._lock:
            if not self._done:
                raise ScanInProgressError("已有扫描正在运行，请先停止或等待完成")
            self._task_id += 1
            task_id = self._task_id
            cancel_event = threading.Event()
            self._cancel_event = cancel_event
            self._logs = []
            self._log_base_offset = 0
            self._live_hits = []
            self._live_offset = 0
            self._progress = {
                "completed": 0,
                "total": 0,
                "hits": 0,
                "current_code": "",
                "current_name": "",
            }
            self._done = False
            self._outcome = "running"
            self._append_locked(f"任务 #{task_id} 已启动")
            self._append_locked(f"最高股价：{config.max_price:g} 元")

        thread = threading.Thread(target=self._run_task, args=(task_id, config, cancel_event), daemon=True)
        thread.start()
        return task_id

    def cancel(self) -> bool:
        with self._lock:
            if self._done or self._cancel_event is None:
                return False
            self._cancel_event.set()
            self._append_locked("收到停止请求，正在安全结束扫描")
            return True

    def snapshot(self, offset: int = 0, hit_offset: int = 0) -> tuple[int, list[str], bool, int, dict, list[dict], int]:
        with self._lock:
            relative_offset = max(0, offset - self._log_base_offset)
            logs = self._logs[relative_offset:]
            hits = self._live_hits[hit_offset:]
            hit_off = len(self._live_hits)
            next_offset = self._log_base_offset + len(self._logs)
            return self._task_id, logs, self._done, next_offset, dict(self._progress), hits, hit_off

    def status(self) -> dict:
        with self._lock:
            return {
                "task_id": self._task_id,
                "logs": list(self._logs),
                "progress": dict(self._progress),
                "done": self._done,
                "outcome": self._outcome,
                "live_hits": list(self._live_hits),
            }

    def _run_task(self, task_id: int, config: StrategyConfig, cancel_event: threading.Event) -> None:
        scan_id = None
        outcome = "failed"
        try:
            scan_id = db.create_scan(config)
            self._append_for(task_id, f"DB写入完成 (scan_id={scan_id})，开始连接数据源...")
            candidates = run_baostock_scan(
                config,
                log=lambda message: self._append_for(task_id, message),
                progress=lambda **values: self._update_progress_for(task_id, **values),
                stop_event=cancel_event,
            )
        except ScanCancelled as exc:
            outcome = "cancelled"
            if scan_id is not None:
                db.finish_scan(scan_id, 0, "cancelled", str(exc))
            self._append_for(task_id, "上一轮扫描已取消")
        except Exception as exc:
            outcome = "failed"
            if scan_id is not None:
                db.finish_scan(scan_id, 0, "failed", str(exc))
            self._append_for(task_id, f"扫描失败：{exc}")
        else:
            if cancel_event.is_set():
                outcome = "cancelled"
                db.finish_scan(scan_id, 0, "cancelled", "扫描已取消")
                self._append_for(task_id, "上一轮扫描已取消")
            else:
                db.complete_scan(scan_id, candidates)
                outcome = "success"
                self._append_for(task_id, f"结果已保存：{len(candidates)} 只候选股")
                if candidates:
                    top = candidates[0]
                    self._append_for(task_id, f"最高分：{top['code']} {top['name']}，{top['score']} 分")
        finally:
            with self._lock:
                if task_id == self._task_id:
                    self._done = True
                    self._outcome = outcome

    def append(self, message: str) -> None:
        with self._lock:
            self._append_locked(message)

    def add_hit(self, item: dict) -> None:
        with self._lock:
            self._live_hits.append(item)

    def update_progress(self, **values) -> None:
        with self._lock:
            detail = values.pop("hits_detail", None)
            self._progress.update(values)
            if detail:
                self._live_hits.append(detail)

    def _append_for(self, task_id: int, message: str) -> None:
        with self._lock:
            if task_id == self._task_id:
                self._append_locked(message)

    def _update_progress_for(self, task_id: int, **values) -> None:
        with self._lock:
            if task_id != self._task_id:
                return
            detail = values.pop("hits_detail", None)
            self._progress.update(values)
            if detail:
                self._live_hits.append(detail)

    def _append_locked(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._logs.append(f"[{timestamp}] {message}")
        if len(self._logs) > 1200:
            overflow = len(self._logs) - 1200
            self._logs = self._logs[overflow:]
            self._log_base_offset += overflow


scan_manager = ScanTaskManager()


class ScanInProgressError(RuntimeError):
    pass
