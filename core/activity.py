"""Трекер активности пользователя для адаптивного планировщика.

Парсинг идёт автономно всегда; когда есть активный логин — чаще, когда нет — реже.
Хранится в памяти процесса (web и scheduler в одном процессе), сбрасывается на рестарте.
"""
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_last_activity = None  # datetime UTC последнего аутентифицированного запроса


def mark_active():
    """Отметить активность: вызывается на каждом запросе с валидной сессией."""
    global _last_activity
    with _lock:
        _last_activity = datetime.now(timezone.utc)


def seconds_since_active():
    """Секунд с последней активности, или None если активности ещё не было."""
    with _lock:
        if _last_activity is None:
            return None
        return (datetime.now(timezone.utc) - _last_activity).total_seconds()


def is_active(window_seconds: int) -> bool:
    """True, если активный логин был в пределах window_seconds."""
    s = seconds_since_active()
    return s is not None and s <= window_seconds
