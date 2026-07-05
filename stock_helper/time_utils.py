"""Timezone-safe clock helpers; never depend on the server's local timezone."""

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def shanghai_now_text() -> str:
    return shanghai_now().strftime("%Y-%m-%d %H:%M:%S")


def shanghai_today():
    return shanghai_now().date()
