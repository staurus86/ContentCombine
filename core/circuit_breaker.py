"""Thread-safe circuit breaker for API services and pipeline stop control."""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Thread-safe pipeline stop event
_pipeline_stop_event = threading.Event()

# Circuit breaker: consecutive API failures per service (thread-safe)
_cb_lock = threading.Lock()
_api_failures = {}  # {service: consecutive_count}
_api_failure_times = {}  # {service: timestamp of last failure}
_API_FAILURE_THRESHOLD = 5  # after 5 consecutive failures, skip service
_CIRCUIT_RESET_SECONDS = 300  # auto-reset after 5 minutes


def _api_circuit_open(service: str) -> bool:
    """Returns True if circuit is open (too many failures, should skip).
    Auto-resets after _CIRCUIT_RESET_SECONDS to avoid permanent deadlock."""
    with _cb_lock:
        if _api_failures.get(service, 0) < _API_FAILURE_THRESHOLD:
            return False
        last_failure = _api_failure_times.get(service, 0)
        if time.time() - last_failure > _CIRCUIT_RESET_SECONDS:
            _api_failures[service] = 0
            logger.info("Circuit breaker AUTO-RESET for %s after %ds timeout", service, _CIRCUIT_RESET_SECONDS)
            return False
        return True


def _api_record_failure(service: str):
    """Records an API failure."""
    with _cb_lock:
        _api_failures[service] = _api_failures.get(service, 0) + 1
        _api_failure_times[service] = time.time()
        if _api_failures[service] == _API_FAILURE_THRESHOLD:
            logger.warning("Circuit breaker OPEN for %s after %d consecutive failures", service, _API_FAILURE_THRESHOLD)


def _api_record_success(service: str):
    """Resets failure counter on success."""
    with _cb_lock:
        _api_failures[service] = 0


def pipeline_stop():
    """Signal pipeline to stop."""
    _pipeline_stop_event.set()


def pipeline_reset():
    """Reset pipeline stop flag."""
    _pipeline_stop_event.clear()


def is_pipeline_stopped() -> bool:
    return _pipeline_stop_event.is_set()


def get_circuit_status() -> dict:
    """Состояние circuit breaker'ов для дашборда."""
    with _cb_lock:
        now = time.time()
        result = {}
        for service in set(list(_api_failures.keys()) + list(_api_failure_times.keys())):
            failures = _api_failures.get(service, 0)
            last_time = _api_failure_times.get(service, 0)
            is_open = failures >= _API_FAILURE_THRESHOLD and (now - last_time <= _CIRCUIT_RESET_SECONDS)
            result[service] = {
                "open": is_open,
                "failures": failures,
                "seconds_until_reset": max(0, int(_CIRCUIT_RESET_SECONDS - (now - last_time))) if is_open else 0,
            }
        return result
