"""Трекер здоровья источников: автоотключение при сбоях, автовключение при восстановлении."""

import logging
import threading
import time
from collections import deque

import config

logger = logging.getLogger(__name__)


def classify_error(error: str) -> str:
    """Classify raw error string into a category."""
    err = str(error).lower()
    if any(k in err for k in ["timeout", "timed out", "read timed out"]):
        return "timeout"
    if any(k in err for k in ["name resolution", "dns", "getaddrinfo", "nodename nor servname"]):
        return "dns"
    if any(k in err for k in ["401", "unauthorized", "forbidden", "403"]):
        return "auth"
    if any(k in err for k in ["404", "not found", "410 gone"]):
        return "http_4xx"
    if any(k in err for k in ["500", "502", "503", "504", "internal server error", "bad gateway", "service unavailable"]):
        return "http_5xx"
    if any(k in err for k in ["429", "rate limit", "too many requests"]):
        return "rate_limit"
    if any(k in err for k in ["connection refused", "connection reset", "broken pipe", "connection aborted"]):
        return "connection"
    if any(k in err for k in ["ssl", "certificate", "handshake"]):
        return "ssl"
    if any(k in err for k in ["json", "parse", "decode", "xml", "encoding"]):
        return "parse_error"
    return "unknown"


class SourceHealth:
    def __init__(self, threshold=5, cooldown=600):
        """
        threshold: сколько подряд ошибок для отключения
        cooldown: через сколько секунд пробуем снова (probe)
        """
        # RLock для безопасного вызова is_healthy() из get_status()
        self._lock = threading.RLock()
        self._threshold = threshold
        self._cooldown = cooldown
        self._sources: dict[str, dict] = {}
        self._latencies: dict[str, deque] = {}  # last 5 latencies per source

    def _ensure(self, source: str):
        if source not in self._sources:
            self._sources[source] = {
                "failures": 0, "total_failures": 0, "total_success": 0,
                "disabled_at": None, "last_error": "", "error_type": "",
            }

    def record_success(self, source: str, latency_ms: float = 0):
        with self._lock:
            self._ensure(source)
            s = self._sources[source]
            was_disabled = s["disabled_at"] is not None
            s["failures"] = 0
            s["total_success"] += 1
            s["disabled_at"] = None
            s["last_error"] = ""
            if latency_ms > 0:
                if source not in self._latencies:
                    self._latencies[source] = deque(maxlen=5)
                self._latencies[source].append(latency_ms)
            if was_disabled:
                logger.info("Source RECOVERED: %s", source)

    def record_failure(self, source: str, error: str = ""):
        with self._lock:
            self._ensure(source)
            s = self._sources[source]
            s["failures"] += 1
            s["total_failures"] += 1
            s["last_error"] = str(error)[:200]
            s["error_type"] = classify_error(error)
            if s["failures"] >= self._threshold:
                if s["disabled_at"] is None:
                    logger.warning("Source DISABLED: %s after %d consecutive failures: %s",
                                   source, s["failures"], error)
                # Always refresh disabled_at to restart cooldown timer (including after failed probe)
                s["disabled_at"] = time.time()

    def is_healthy(self, source: str) -> bool:
        with self._lock:
            self._ensure(source)
            s = self._sources[source]
            if s["disabled_at"] is None:
                return True
            if time.time() - s["disabled_at"] > self._cooldown:
                logger.info("Source PROBE: %s — trying again after %ds cooldown",
                            source, self._cooldown)
                return True
            return False

    def avg_latency(self, source: str) -> float:
        """Return average latency in ms for last 5 requests. 0 if no data."""
        with self._lock:
            lat = self._latencies.get(source)
            if not lat:
                return 0
            return sum(lat) / len(lat)

    def is_slow(self, source: str, threshold_ms: float = 30000) -> bool:
        """Return True if average latency exceeds threshold (default 30s)."""
        avg = self.avg_latency(source)
        return avg > threshold_ms if avg > 0 else False

    def get_status(self) -> dict:
        """Возвращает статус всех источников. Safe: RLock позволяет вызов is_healthy()."""
        with self._lock:
            return {
                name: {
                    "healthy": self.is_healthy(name),
                    "consecutive_failures": s["failures"],
                    "total_failures": s["total_failures"],
                    "total_success": s["total_success"],
                    "last_error": s["last_error"],
                    "error_type": s["error_type"],
                    "disabled_at": s["disabled_at"],
                    "avg_latency_ms": self.avg_latency(name),
                }
                for name, s in self._sources.items()
            }


# Глобальный инстанс
source_health = SourceHealth(threshold=config.SOURCE_FAILURE_THRESHOLD, cooldown=config.SOURCE_PROBE_COOLDOWN)
