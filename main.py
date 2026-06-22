import logging
import signal
import sys
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

import config
from storage.database import init_db
from storage.sheets import setup_headers
from scheduler import start_scheduler
from web import start_web
from apis.cache import setup_dashboard_logging


def _handle_shutdown(signum, frame):
    """Graceful shutdown with 30s timeout."""
    logging.info("Received signal %s, shutting down...", signum)
    try:
        from scheduler import pipeline_stop
        pipeline_stop()
    except Exception as e:
        logging.warning("pipeline_stop() failed: %s", e)

    # Force exit after 30s if graceful shutdown hangs
    def _force_exit():
        import time; time.sleep(30)
        logging.error("Shutdown timed out (30s), forcing exit")
        import os; os._exit(1)

    threading.Thread(target=_force_exit, daemon=True).start()
    sys.exit(0)


def main():
    logging.info("IgroNews starting...")

    # Graceful shutdown on SIGTERM (Docker/Railway) and SIGINT (Ctrl+C)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    # Подключаем логи для дашборда
    setup_dashboard_logging()

    # Structured logging with correlation IDs
    try:
        from core.observability import setup_structured_logging
        setup_structured_logging()
    except Exception as e:
        logging.warning("Structured logging init skipped: %s", e)

    # Инициализация БД
    init_db()

    # Load persistent settings from DB (overrides env defaults)
    from config import load_persistent_settings
    load_persistent_settings()

    # Startup health check
    try:
        from storage.database import db_cursor
        with db_cursor() as cur:
            cur.execute("SELECT 1")
        logging.info("Startup: DB OK")
    except Exception as e:
        logging.error("Startup: DB failed: %s. Retrying in 10s...", e)
        import time as _t; _t.sleep(10)
        try:
            init_db()
            logging.info("Startup: DB reconnected")
        except Exception:
            logging.critical("Cannot connect to database. Exiting.")
            sys.exit(1)

    # Создание заголовков в Sheets (если пусто)
    setup_headers()

    # Запуск веб-дашборда на порту 8080
    start_web()
    logging.info("Dashboard running on port 8080")

    # Запуск Telegram-бота (если токен задан)
    if config.TELEGRAM_BOT_TOKEN:
        from bot.telegram_bot import start_bot_polling
        start_bot_polling()
        logging.info("Telegram bot started")

    # Запуск планировщика
    start_scheduler()


if __name__ == "__main__":
    main()
