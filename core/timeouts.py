"""Таймауты для job'ов и API-вызовов.

ВАЖНО: run_with_timeout запускает функцию в daemon thread. При таймауте
поток продолжает жить (Python не может убить thread). Для мониторинга
утечек используется set живых zombie threads (не кумулятивный счётчик).
"""

import logging
import threading
from functools import wraps

logger = logging.getLogger(__name__)

_zombie_lock = threading.Lock()
_zombie_threads: set[threading.Thread] = set()
_zombie_total = 0  # cumulative, for logging only


def get_zombie_thread_count() -> int:
    """Return count of currently alive zombie threads (not cumulative)."""
    with _zombie_lock:
        # Purge threads that have since finished
        _zombie_threads.discard(None)
        dead = {t for t in _zombie_threads if not t.is_alive()}
        _zombie_threads.difference_update(dead)
        return len(_zombie_threads)


def run_with_timeout(fn, args=(), kwargs=None, timeout=30, default=None, label=""):
    """Запускает fn в daemon thread с таймаутом.
    Возвращает результат fn или default при таймауте/ошибке.
    """
    global _zombie_total
    kwargs = kwargs or {}
    result_holder = [default]
    error_holder = [None]

    def wrapper():
        try:
            result_holder[0] = fn(*args, **kwargs)
        except Exception as e:
            error_holder[0] = e

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        name = label or getattr(fn, '__name__', str(fn))
        with _zombie_lock:
            _zombie_threads.add(t)
            _zombie_total += 1
        logger.error("TIMEOUT (%ds): %s — zombie thread #%d (alive: %d)",
                      timeout, name, _zombie_total, get_zombie_thread_count())
        return default

    if error_holder[0]:
        name = label or getattr(fn, '__name__', str(fn))
        logger.warning("ERROR in %s: %s", name, error_holder[0])
        return default

    return result_holder[0]


def with_timeout(timeout=30, default=None):
    """Декоратор: оборачивает функцию в run_with_timeout."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return run_with_timeout(fn, args=args, kwargs=kwargs,
                                    timeout=timeout, default=default,
                                    label=fn.__name__)
        return wrapper
    return decorator
