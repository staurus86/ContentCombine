"""Scheduler: APScheduler job configuration and source parsing.

Business logic lives in:
- core/circuit_breaker.py  — circuit breaker + pipeline stop
- pipeline/orchestrator.py — full-auto, no-LLM, enrichment, task queue
"""

import gc
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

import config
from parsers.rss_parser import parse_rss_source
from parsers.html_parser import parse_html_source
from storage.database import cleanup_old_plaintext, cleanup_old_tasks, log_health_snapshot

# Re-export for backward compatibility (web.py, bot, tests import from scheduler)
from core.circuit_breaker import (  # noqa: F401
    _api_circuit_open, _api_record_failure, _api_record_success,
    pipeline_stop, pipeline_reset, is_pipeline_stopped,
)
from pipeline.orchestrator import (  # noqa: F401
    _auto_review_new, _auto_rescore_zero,  # NOTE: _auto_rescore_zero has LIMIT 200 inside orchestrator.py
    process_news, _process_single_news, _do_process,
    _update_task, _create_task, _fetch_news_by_id, _fetch_analysis_by_id,
    _calc_final_score, run_full_auto_pipeline, run_no_llm_pipeline,
    _save_rewrite_article, _build_check_result_from_analysis,
    generate_auto_digest, publish_scheduled_articles,
    FULL_AUTO_SCORE_THRESHOLD, FULL_AUTO_FINAL_THRESHOLD,
)

logger = logging.getLogger(__name__)
RUNNING_SCHEDULER = None


def parse_sources(interval_min: int):
    """Parse all sources with the given interval. Error-isolated per source."""
    from core.watchdog import watchdog
    from core.source_health import source_health
    from core.timeouts import run_with_timeout

    sources = [s for s in config.SOURCES if s["interval"] == interval_min]
    total = 0
    failed = 0

    for source in sources:
        name = source.get("name", source.get("url", "unknown"))

        # Skip unhealthy sources (auto-disabled after consecutive failures)
        if not source_health.is_healthy(name):
            logger.debug("Skipping unhealthy source: %s", name)
            continue

        # Skip manually disabled sources (toggled from dashboard)
        from core.feature_flags import get_disabled_sources
        disabled = get_disabled_sources()
        if name in disabled:
            logger.debug("Skipping manually disabled source: %s", name)
            continue

        try:
            def _parse_one(src=source):
                if src["type"] == "rss":
                    return parse_rss_source(src)
                elif src["type"] in ("html", "dtf", "gamesradar", "homepage"):
                    return parse_html_source(src)
                elif src["type"] == "sitemap":
                    from parsers.html_parser import parse_sitemap_source
                    return parse_sitemap_source(src)
                elif src["type"] == "vk":
                    from parsers.vk_parser import parse_vk_source
                    return parse_vk_source(src)
                elif src["type"] == "telegram":
                    from parsers.telegram_parser import parse_telegram_source
                    return parse_telegram_source(src)
                elif src["type"] == "bluesky":
                    from parsers.bluesky_parser import parse_bluesky_source
                    return parse_bluesky_source(src)
                return 0

            count = run_with_timeout(_parse_one, timeout=90, default=None,
                                     label=f"parse:{name}")
            if count is None:
                source_health.record_failure(name, "timeout or error")
                failed += 1
            else:
                total += count
                source_health.record_success(name)
        except Exception as e:
            logger.error("Parser error [%s]: %s", name, e)
            source_health.record_failure(name, str(e))
            failed += 1

    logger.info("[%dmin] Parsed: %d new, %d failed sources", interval_min, total, failed)
    watchdog.heartbeat("scheduler", f"parsed {total} new, {failed} failed")

    gc.collect()

    if total > 0:
        try:
            _auto_review_new()
        except Exception as e:
            logger.error("Auto-review error (non-fatal): %s", e)


def _recover_stuck_tasks():
    """Reset tasks stuck in 'running' for >30 minutes back to 'pending'."""
    from storage.database import db_cursor, ph, get_connection, _is_postgres
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with db_cursor() as cur:
        cur.execute(f"UPDATE task_queue SET status = 'pending', updated_at = {ph()} WHERE status = 'running' AND updated_at < {ph()}",
                    (datetime.now(timezone.utc).isoformat(), cutoff))
        if not _is_postgres():
            get_connection().commit()
        count = cur.rowcount
    if count:
        logger.warning("RECOVERY: reset %d stuck tasks (running > 30min) back to pending", count)


def _cancel_expired_scheduled():
    """Cancel scheduled articles that are >48h overdue."""
    from storage.database import db_cursor, ph, get_connection, _is_postgres
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    with db_cursor() as cur:
        cur.execute(f"UPDATE articles SET status = 'cancelled' WHERE status = 'scheduled' AND scheduled_at < {ph()}", (cutoff,))
        if not _is_postgres():
            get_connection().commit()
        count = cur.rowcount
    if count:
        logger.info("Cancelled %d overdue scheduled articles (>48h past)", count)


def start_scheduler():
    """Start the APScheduler with all configured jobs."""
    global RUNNING_SCHEDULER
    scheduler = BlockingScheduler(timezone="Europe/Moscow")
    RUNNING_SCHEDULER = scheduler

    # Parsing by interval (includes auto-review)
    intervals = sorted(set(s["interval"] for s in config.SOURCES))
    for mins in intervals:
        scheduler.add_job(parse_sources, "interval", minutes=mins, args=[mins], id=f"parse_{mins}min")

    # Cleanup old plain_text daily (7 days)
    scheduler.add_job(lambda: cleanup_old_plaintext(days=7), "interval", hours=24, id="cleanup_plaintext")

    # Cleanup old tasks from task_queue daily
    scheduler.add_job(cleanup_old_tasks, "interval", hours=24, id="cleanup_tasks")

    # Auto-purge soft-deleted news older than 30 days
    from api.news import auto_purge_old_deleted
    scheduler.add_job(lambda: auto_purge_old_deleted(days=30), "interval", hours=24, id="auto_purge_deleted")

    # Auto-delete short news (< 100 chars title)
    from api.news import cleanup_short_news
    scheduler.add_job(lambda: cleanup_short_news(100), "interval", hours=6, id="cleanup_short_news")

    # Cache cleanup every 3 hours
    from apis.cache import cache_cleanup
    scheduler.add_job(cache_cleanup, "interval", hours=3, id="cache_cleanup")

    # Publish scheduled articles every minute
    scheduler.add_job(publish_scheduled_articles, "interval", minutes=1, id="publish_scheduled")

    # Retry failed Sheets exports every 15 minutes
    from pipeline.orchestrator import retry_sheets_exports
    scheduler.add_job(retry_sheets_exports, "interval", minutes=15, id="retry_sheets")

    # Recover tasks stuck in 'running' for >30 minutes
    scheduler.add_job(_recover_stuck_tasks, "interval", minutes=15, id="recover_stuck_tasks")

    # Cancel scheduled articles that are >48h overdue
    scheduler.add_job(_cancel_expired_scheduled, "interval", hours=1, id="cancel_expired_scheduled")

    # Auto-rescore news with score=0: daily at 04:00
    scheduler.add_job(_auto_rescore_zero, "cron", hour=4, minute=0, id="auto_rescore_zero")

    # Auto-digest: daily at 23:00 Moscow time
    scheduler.add_job(generate_auto_digest, "cron", hour=23, minute=0, id="auto_digest")

    # Storylines daily export: use dashboard settings if auto-export enabled,
    # otherwise fall back to hardcoded 09:00 schedule.
    try:
        from api.dashboard import get_storylines_settings, export_storylines_to_sheets
        sl_settings = get_storylines_settings()
        if sl_settings.get("enabled"):
            # Dashboard auto-export is configured — use its settings
            sl_hour = sl_settings.get("hour", 9)
            sl_minute = sl_settings.get("minute", 0)
            sl_days = sl_settings.get("days", 3)
            scheduler.add_job(
                lambda: export_storylines_to_sheets(days=sl_days, trigger="auto"),
                "cron", hour=sl_hour, minute=sl_minute,
                id="storylines_auto_export", replace_existing=True,
            )
            logger.info("Storylines auto-export scheduled: %02d:%02d daily, %d days (from settings)", sl_hour, sl_minute, sl_days)
        else:
            # Fallback: hardcoded 09:00 MSK (06:00 UTC)
            scheduler.add_job(
                lambda: export_storylines_to_sheets(
                    days=get_storylines_settings().get("days", 3),
                    trigger="daily_fallback",
                ),
                "cron", hour=6, minute=0,
                id="storylines_daily_export_9msk", replace_existing=True,
            )
            logger.info("Storylines daily export scheduled: 06:00 UTC (fallback)")
    except Exception as e:
        logger.warning("Storylines daily export init skipped: %s", e)

    # Watchdog: periodic health check + recovery actions
    from core.watchdog import watchdog

    def _recovery_parse_restart():
        """Recovery: re-trigger parsing for all intervals."""
        logger.warning("RECOVERY: re-triggering parse for all intervals")
        gc.collect()
        intervals = sorted(set(s["interval"] for s in config.SOURCES))
        for mins in intervals:
            try:
                parse_sources(mins)
            except Exception as e:
                logger.error("RECOVERY parse %dmin failed: %s", mins, e)

    watchdog.register_recovery("scheduler", _recovery_parse_restart)

    def _watchdog_check():
        watchdog.run_recovery()
        health = watchdog.check_health()
        stale = [name for name, v in health.items() if v["stale"]]
        if stale:
            logger.warning("WATCHDOG: stale components: %s", stale)
        from core.timeouts import get_zombie_thread_count
        zombies = get_zombie_thread_count()
        if zombies > 0:
            logger.warning("WATCHDOG: %d zombie threads detected", zombies)
        # Emergency: too many zombie threads — force restart (Railway will auto-restart)
        if zombies > 5:
            logger.critical("WATCHDOG: %d zombie threads — forcing process restart", zombies)
            import os; os._exit(1)
        # Mass source failure alert
        from core.source_health import source_health
        status = source_health.get_status()
        down_count = sum(1 for s in status.values() if isinstance(s, dict) and s.get("failures", 0) >= 5)
        if down_count >= 3:
            logger.critical("MASS FAILURE: %d sources down simultaneously — check network/DNS", down_count)

    scheduler.add_job(_watchdog_check, "interval", minutes=5, id="watchdog_check")

    # Health log: snapshot every 5 minutes
    scheduler.add_job(log_health_snapshot, "interval", minutes=5, id="health_log")

    # Cleanup health_log entries older than 7 days: daily
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    def _cleanup_health_log():
        from storage.database import db_cursor, ph, get_connection, _is_postgres
        cutoff = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
        with db_cursor() as cur:
            cur.execute(f"DELETE FROM health_log WHERE timestamp < {ph()}", (cutoff,))
            if not _is_postgres():
                get_connection().commit()

    scheduler.add_job(_cleanup_health_log, "interval", hours=24, id="cleanup_health_log")

    # Initial parse on startup (includes auto-review)
    for mins in intervals:
        parse_sources(mins)

    logger.info("Scheduler started")
    try:
        scheduler.start()
    finally:
        RUNNING_SCHEDULER = None
