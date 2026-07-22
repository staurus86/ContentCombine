"""Scheduler: APScheduler job configuration and source parsing.

Business logic lives in:
- core/circuit_breaker.py  — circuit breaker + pipeline stop
- pipeline/orchestrator.py — full-auto, no-LLM, enrichment, task queue
"""

import gc
import logging
import threading

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
    auto_publish_telegram_digest, catchup_tg_digest,
    check_critical_alerts, publish_scheduled_articles,
    FULL_AUTO_SCORE_THRESHOLD, FULL_AUTO_FINAL_THRESHOLD,
)

logger = logging.getLogger(__name__)
RUNNING_SCHEDULER = None

# Sentinel: run_with_timeout returns this on timeout / unhandled error (distinct from
# a parser returning None, which means "I already recorded a specific failure").
_PARSE_FAILED = object()

# Guard against concurrent full-parse cycles. Three paths can launch parsing at once:
# the background initial-parse thread, the scheduled adaptive_parse job, and the
# watchdog recovery action. Overlapping cycles thrash the proxy pool/CPU so no cycle
# ever traverses the full source list — starving most sources. Only one runs at a time;
# any overlapping call returns immediately.
_PARSE_LOCK = threading.Lock()


def _parse_source_list(sources, label: str):
    """Parse the given list of sources. Error-isolated per source.

    Serialized: if a parse cycle is already running, this call is skipped so
    concurrent cycles can't pile up (see _PARSE_LOCK)."""
    from core.watchdog import watchdog
    from core.source_health import source_health
    from core.timeouts import run_with_timeout

    if not _PARSE_LOCK.acquire(blocking=False):
        logger.info("[%s] Parse cycle already running — skipping overlapping run", label)
        return 0
    try:
        return _parse_source_list_locked(sources, label, watchdog, source_health, run_with_timeout)
    finally:
        _PARSE_LOCK.release()


def _parse_source_list_locked(sources, label, watchdog, source_health, run_with_timeout):
    total = 0
    failed = 0

    for idx, source in enumerate(sources):
        name = source.get("name", source.get("url", "unknown"))

        # Heartbeat mid-cycle: a full traversal takes longer than the watchdog stale
        # timeout (300s), so without this the watchdog would treat a healthy long cycle
        # as dead and fire recovery — spawning the very overlap _PARSE_LOCK prevents.
        if idx % 10 == 0:
            watchdog.heartbeat("scheduler", f"parsing {idx}/{len(sources)} [{label}]")

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
                elif src["type"] in ("html", "homepage"):
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

            count = run_with_timeout(_parse_one, timeout=90, default=_PARSE_FAILED,
                                     label=f"parse:{name}")
            if count is _PARSE_FAILED:
                # timeout, or an unhandled error swallowed by run_with_timeout
                source_health.record_failure(name, "read timed out")
                failed += 1
            elif count is None:
                # parser detected a specific failure and already recorded it (DNS/404/parse…)
                failed += 1
            else:
                total += count
                source_health.record_success(name)
        except Exception as e:
            logger.error("Parser error [%s]: %s", name, e)
            source_health.record_failure(name, str(e))
            failed += 1

    logger.info("[%s] Parsed: %d new, %d failed sources", label, total, failed)
    watchdog.heartbeat("scheduler", f"parsed {total} new, {failed} failed")

    gc.collect()

    if total > 0:
        try:
            _auto_review_new()
        except Exception as e:
            logger.error("Auto-review error (non-fatal): %s", e)
    return total


def parse_sources(interval_min: int):
    """Parse sources whose interval == interval_min (used by watchdog recovery)."""
    srcs = [s for s in config.SOURCES if s["interval"] == interval_min]
    return _parse_source_list(srcs, f"{interval_min}min")


def parse_all_sources():
    """Parse ALL configured sources — used by the adaptive cadence controller."""
    return _parse_source_list(list(config.SOURCES), "all")


_LAST_FULL_PARSE = None


def adaptive_parse_tick():
    """Адаптивная автономная частота парсинга: есть активный логин →
    PARSE_ACTIVE_MIN, иначе PARSE_IDLE_MIN. Тик запускается каждые
    PARSE_ACTIVE_MIN минут и внутри гейтит реальный парс по времени —
    так цикл не наслаивается, а частота меняется на лету."""
    global _LAST_FULL_PARSE
    from datetime import datetime, timezone, timedelta
    from core.activity import is_active

    # Heartbeat КАЖДЫЙ тик, а не только внутри парса: раньше между циклами
    # (15/60 мин) «scheduler» протухал за WATCHDOG_STALE_TIMEOUT, watchdog вечно
    # считал его мёртвым и форсил recovery-парс каждые ~5 мин — idle-каденция не
    # работала, WARNING-спам скрывал реальные деградации. Живой тик = живой шедулер.
    try:
        from core.watchdog import watchdog
        watchdog.heartbeat("scheduler", "adaptive tick")
    except Exception:
        pass

    active = is_active(config.PARSE_ACTIVE_WINDOW_MIN * 60)

    # Тихий режим: без активного логина парсим ТОЛЬКО в окне PARSE_IDLE_WINDOW_HOURS часов
    # перед авто-дайджестом (МСК) — собрать сутки к дайджесту, не гонять процесс весь день.
    # Активный логин (заход в UI) будит парс в любое время: свежесть ленты при работе цела.
    if not active and config.PARSE_IDLE_WINDOW_HOURS < 24:
        msk_hour = (datetime.now(timezone.utc) + timedelta(hours=3)).hour
        digest_hour = config.AUTO_DIGEST_CRON_HOUR
        win_start = (digest_hour - config.PARSE_IDLE_WINDOW_HOURS) % 24
        if win_start < digest_hour:
            in_window = win_start <= msk_hour < digest_hour
        else:  # окно пересекает полночь
            in_window = msk_hour >= win_start or msk_hour < digest_hour
        if not in_window:
            logger.debug("Idle parse skip: вне окна %02d:00–%02d:00 МСК (сейчас %02d МСК)",
                         win_start, digest_hour, msk_hour)
            return

    eff_min = config.PARSE_ACTIVE_MIN if active else config.PARSE_IDLE_MIN
    now = datetime.now(timezone.utc)
    if _LAST_FULL_PARSE is not None and (now - _LAST_FULL_PARSE).total_seconds() < eff_min * 60 - 20:
        logger.debug("Adaptive parse skip: cadence=%dmin (active=%s)", eff_min, active)
        return
    logger.info("Adaptive parse: cadence=%dmin (active=%s)", eff_min, active)
    parse_all_sources()
    _LAST_FULL_PARSE = now


def _cleanup_history_retention():
    """Ретеншн накопительных таблиц — иначе растут вечно (аудит автономности
    2026-07-05). Только удаление СТАРОГО, порогом с большим запасом к чтению:
    digest_history читается на 2 дня — держим 30; ai_citations сравнивается
    неделя-к-неделе — держим 365; digests (сохранённые дайджесты UI) — 180;
    decision_trace — 30; api_cost_log — 180. config_audit/tg_subs_history не
    трогаем — маленькие и это аудит-след. Fail-open per-table."""
    from datetime import datetime, timezone, timedelta
    from storage.database import get_connection, _is_postgres
    ph = "%s" if _is_postgres() else "?"
    plans = [
        ("digest_history", "created_at", 30),
        ("ai_citations", "created_at", 365),
        ("digests", "created_at", 180),
        ("decision_trace", "created_at", 30),
        ("api_cost_log", "created_at", 180),
    ]
    conn = get_connection()
    cur = conn.cursor()
    try:
        for table, col, days in plans:
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                cur.execute(f"DELETE FROM {table} WHERE {col} < {ph}", (cutoff,))
                if cur.rowcount:
                    logger.info("Retention: %s — removed %d rows older than %dd", table, cur.rowcount, days)
            except Exception as e:
                logger.debug("Retention skip %s: %s", table, e)
        if not _is_postgres():
            conn.commit()
    finally:
        cur.close()


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

    # Adaptive autonomous parsing: активный логин → PARSE_ACTIVE_MIN, иначе PARSE_IDLE_MIN.
    # Тик идёт на быстрой частоте и гейтит реальный парс внутри; max_instances=1 не даёт
    # циклам наслаиваться, если полный обход источников длится дольше одного тика.
    scheduler.add_job(adaptive_parse_tick, "interval", minutes=config.PARSE_ACTIVE_MIN,
                      id="adaptive_parse", max_instances=1, coalesce=True)

    # Cleanup old plain_text daily (7 days)
    scheduler.add_job(lambda: cleanup_old_plaintext(days=7), "interval", hours=24, id="cleanup_plaintext")

    # Cleanup old tasks from task_queue daily
    scheduler.add_job(cleanup_old_tasks, "interval", hours=24, id="cleanup_tasks")

    # Ретеншн накопительных таблиц (digest_history/ai_citations/digests/
    # decision_trace/api_cost_log) — без него растут вечно.
    scheduler.add_job(_cleanup_history_retention, "interval", hours=24, id="history_retention")

    # Auto-purge soft-deleted news older than 30 days
    from api.news import auto_purge_old_deleted
    scheduler.add_job(lambda: auto_purge_old_deleted(days=30), "interval", hours=24, id="auto_purge_deleted")

    # Auto-delete near-empty titles only (nav/junk like "News"/"Tags").
    # Порог низкий (12), чтобы НЕ терять реальные короткие SEO-заголовки
    # (напр. «Google confirms core update» = 27 симв.). Кейсы защищены отдельно.
    from api.news import cleanup_short_news
    scheduler.add_job(lambda: cleanup_short_news(12), "interval", hours=6, id="cleanup_short_news")

    # Freshness: soft-delete news older than NEWS_MAX_AGE_DAYS (by article date) to trash
    from api.news import soft_delete_stale_news
    scheduler.add_job(soft_delete_stale_news, "interval", hours=6, id="soft_delete_stale_news")
    try:
        soft_delete_stale_news()  # run once on startup
    except Exception as e:
        logger.warning("Initial stale-news sweep skipped: %s", e)

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

    # Auto digest → Telegram channel: daily at AUTO_DIGEST_CRON_HOUR:00 Moscow time
    # (scheduler tz = Europe/Moscow). Builds the GENERAL digest (best of feed + cases
    # + telegram, 24h) and publishes it. Changing the setting takes effect on restart.
    scheduler.add_job(auto_publish_telegram_digest, "cron",
                      hour=config.AUTO_DIGEST_CRON_HOUR, minute=0, id="auto_tg_digest")

    # Self-heal: if a deploy restarted the in-memory scheduler right at 20:00 and the
    # cron run was lost, this catch-up republishes once (idempotent via the date marker).
    # First check ~60s after start (not the default 5 min) so recovery is fast.
    from datetime import datetime as _dt_catch, timedelta as _td_catch
    scheduler.add_job(catchup_tg_digest, "interval", minutes=5, id="catchup_tg_digest",
                      next_run_time=_dt_catch.now(scheduler.timezone) + _td_catch(seconds=60))

    # Daily subscriber snapshot for TG channels (for the «Подписчики» delta).
    from api.news import refresh_tg_subscribers
    scheduler.add_job(refresh_tg_subscribers, "cron", hour=10, minute=30, id="tg_subs_snapshot")

    # Недельный дайджест → TG-канал: воскресенье WEEKLY_DIGEST_CRON_HOUR:00 МСК
    # (general за 7 дней + секция «Кого цитирует AI»). Идемпотентно по ISO-неделе;
    # catch-up дошлёт в то же воскресенье, если слот задел деплой.
    from pipeline.orchestrator import auto_publish_weekly_digest, catchup_weekly_digest
    scheduler.add_job(auto_publish_weekly_digest, "cron", day_of_week="sun",
                      hour=config.WEEKLY_DIGEST_CRON_HOUR, minute=0, id="auto_tg_weekly_digest")
    scheduler.add_job(catchup_weekly_digest, "interval", minutes=15, id="catchup_weekly_digest")

    # AI-цитируемость (Sprint 5): еженедельный скан — кого цитируют AI-поисковики
    # по нашим SEO/GEO-запросам. ВОСКРЕСЕНЬЕ 07:00 МСК — до недельного дайджеста
    # (вс 19:00), чтобы секция «Кого цитирует AI» уходила со свежими данными дня,
    # а не 6-дневной давности (изначально стоял понедельник). Без рабочих движков
    # (Perplexity-ключ / search-модели на гейтвее) скан честно выходит no_engine.
    def _weekly_citability_scan():
        try:
            from apis.ai_citability import run_citability_scan
            res = run_citability_scan()
            logger.info("Weekly citability scan: %s", res.get("status"))
        except Exception as e:
            logger.warning("Weekly citability scan failed: %s", e)
    scheduler.add_job(_weekly_citability_scan, "cron", day_of_week="sun",
                      hour=7, minute=0, id="ai_citability_scan")

    # Operational alerts → admin Telegram: new critical incidents + mass source failure.
    scheduler.add_job(check_critical_alerts, "interval", minutes=10, id="critical_alerts")

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
        logger.warning("RECOVERY: re-triggering parse for all sources")
        gc.collect()
        try:
            parse_all_sources()
        except Exception as e:
            logger.error("RECOVERY parse failed: %s", e)

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

    # Initial parse on startup — in a BACKGROUND thread so a slow full parse
    # (144 sources, many slow/failing) does NOT delay scheduler.start() and the
    # cron jobs (esp. the 20:00 publish). Previously this blocked startup by minutes.
    import threading as _threading_init
    _threading_init.Thread(target=adaptive_parse_tick, daemon=True, name="initial-parse").start()

    logger.info("Scheduler started")
    try:
        scheduler.start()
    finally:
        RUNNING_SCHEDULER = None
