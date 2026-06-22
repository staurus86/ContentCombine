"""API cache + rate limiter + retry с exponential backoff."""

import hashlib
import json
import logging
import time
import threading
from collections import OrderedDict, deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# --- In-memory cache (TTL-based, thread-safe) ---

_cache = OrderedDict()
_cache_lock = threading.Lock()
_MAX_CACHE_SIZE = 1000  # reduced from 2000 — each entry can be large
_DEFAULT_TTL = 86400  # 24 hours


def cache_get(key: str):
    """Возвращает кэшированное значение или None."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if time.time() > entry["expires"]:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return entry["value"]


def cache_set(key: str, value, ttl: int = _DEFAULT_TTL):
    """Сохраняет значение в кэш."""
    with _cache_lock:
        _cache[key] = {"value": value, "expires": time.time() + ttl}
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHE_SIZE:
            _cache.popitem(last=False)


def cache_key(*args) -> str:
    """Строит ключ кэша из аргументов."""
    raw = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


def get_cache_stats() -> dict:
    """Статистика кэша для дашборда."""
    with _cache_lock:
        now = time.time()
        total = len(_cache)
        alive = sum(1 for e in _cache.values() if now <= e["expires"])
        return {"total": total, "alive": alive, "expired": total - alive, "max": _MAX_CACHE_SIZE}


def cache_cleanup():
    """Удаляет просроченные записи из кэша. Вызывается периодически."""
    with _cache_lock:
        now = time.time()
        expired_keys = [k for k, v in _cache.items() if now > v["expires"]]
        for k in expired_keys:
            _cache.pop(k, None)
    if expired_keys:
        logger.debug("Cache cleanup: removed %d expired entries", len(expired_keys))
    return len(expired_keys)


def clear_cache():
    """Очищает весь кэш."""
    with _cache_lock:
        _cache.clear()


# --- Rate limiter (per-service daily counters) ---

_rate_counters = {}  # {service: {"date": "2025-01-01", "count": 0}}
_rate_lock = threading.Lock()

_RATE_LIMITS = {
    "llm": 500,
    "keyso": 200,
    "trends": 100,
}


def rate_check(service: str) -> bool:
    """Проверяет, не превышен ли лимит. Возвращает True если можно продолжать."""
    limit = _RATE_LIMITS.get(service, 1000)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _rate_lock:
        counter = _rate_counters.get(service)
        if not counter or counter["date"] != today:
            _rate_counters[service] = {"date": today, "count": 0}
            counter = _rate_counters[service]
        return counter["count"] < limit


def rate_increment(service: str):
    """Увеличивает счётчик использования API."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _rate_lock:
        counter = _rate_counters.get(service)
        if not counter or counter["date"] != today:
            _rate_counters[service] = {"date": today, "count": 0}
        _rate_counters[service]["count"] += 1


def get_rate_stats() -> dict:
    """Статистика rate limits для дашборда."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _rate_lock:
        result = {}
        for service, limit in _RATE_LIMITS.items():
            counter = _rate_counters.get(service)
            used = counter["count"] if counter and counter["date"] == today else 0
            result[service] = {"used": used, "limit": limit, "remaining": limit - used}
        return result


# --- Retry with exponential backoff ---

def retry_call(fn, *args, max_retries: int = 3, base_delay: float = 2.0,
               service: str = "", **kwargs):
    """Вызывает fn с retry и exponential backoff.
    Если service указан — проверяет rate limit и считает вызовы.
    """
    if service and not rate_check(service):
        logger.warning("Rate limit exceeded for %s", service)
        return None

    last_error = None
    for attempt in range(max_retries):
        try:
            if service:
                rate_increment(service)
            result = fn(*args, **kwargs)
            return result
        except Exception as e:
            last_error = e
            delay = base_delay * (2 ** attempt)
            logger.warning("Retry %d/%d for %s: %s (next in %.1fs)",
                           attempt + 1, max_retries, fn.__name__, e, delay)
            if attempt < max_retries - 1:
                time.sleep(delay)

    logger.error("All %d retries failed for %s: %s", max_retries, fn.__name__, last_error)
    return None


# --- In-memory log ring buffer (deque = O(1) append/evict vs list.pop(0) = O(n)) ---

_log_buffer = deque(maxlen=300)
_log_lock = threading.Lock()


class DashboardLogHandler(logging.Handler):
    """Кастомный handler для хранения логов в памяти."""

    def emit(self, record):
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            with _log_lock:
                _log_buffer.append(entry)
        except Exception:
            pass


def get_logs(limit: int = 100, level: str = "") -> list[dict]:
    """Возвращает последние логи для дашборда."""
    with _log_lock:
        logs = list(_log_buffer)
    if level:
        level_upper = level.upper()
        logs = [entry for entry in logs if entry["level"] == level_upper]
    return logs[-limit:]


def setup_dashboard_logging():
    """Подключает DashboardLogHandler к root logger."""
    handler = DashboardLogHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
