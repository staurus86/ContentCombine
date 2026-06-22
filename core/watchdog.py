"""Health monitor: heartbeat tracking, stale detection, auto-recovery, system metrics."""

import logging
import threading
import time

import config

logger = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, max_stale_seconds=300):
        self._lock = threading.Lock()
        self._max_stale = max_stale_seconds
        self._components: dict[str, dict] = {}
        self._recovery_actions: dict[str, callable] = {}

    def heartbeat(self, component: str, status: str = "ok"):
        with self._lock:
            self._components[component] = {
                "last_heartbeat": time.time(),
                "status": status,
                "error_count": self._components.get(component, {}).get("error_count", 0),
            }

    def record_error(self, component: str, error: str = ""):
        with self._lock:
            if component not in self._components:
                self._components[component] = {
                    "last_heartbeat": time.time(), "status": "error", "error_count": 0
                }
            self._components[component]["error_count"] += 1
            self._components[component]["status"] = f"error: {str(error)[:100]}"

    def register_recovery(self, component: str, action):
        """Регистрирует функцию восстановления для компонента."""
        with self._lock:
            self._recovery_actions[component] = action

    def check_health(self) -> dict:
        now = time.time()
        with self._lock:
            result = {}
            for name, data in self._components.items():
                age = now - data["last_heartbeat"]
                result[name] = {
                    "stale": age > self._max_stale,
                    "age_seconds": round(age),
                    "status": data["status"],
                    "error_count": data["error_count"],
                }
            return result

    def get_system_health(self) -> dict:
        """Системные метрики: threads, zombie count."""
        try:
            from core.timeouts import get_zombie_thread_count
            zombies = get_zombie_thread_count()
        except ImportError:
            zombies = -1
        return {
            "active_threads": threading.active_count(),
            "zombie_threads": zombies,
            "components": self.check_health(),
        }

    def is_alive(self) -> bool:
        health = self.check_health()
        return bool(health) and all(not v["stale"] for v in health.values())

    def run_recovery(self):
        """Проверяет здоровье и запускает recovery для зависших компонентов."""
        health = self.check_health()
        with self._lock:
            actions = dict(self._recovery_actions)
        for name, status in health.items():
            if status["stale"] and name in actions:
                logger.warning("WATCHDOG: %s stale (%ds), running recovery...",
                               name, status["age_seconds"])
                try:
                    actions[name]()
                    logger.info("WATCHDOG: %s recovery triggered", name)
                except Exception as e:
                    logger.error("WATCHDOG: %s recovery failed: %s", name, e)


# Глобальный инстанс
watchdog = Watchdog(max_stale_seconds=config.WATCHDOG_STALE_TIMEOUT)
