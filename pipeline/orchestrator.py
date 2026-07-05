"""Pipeline orchestration: full-auto, no-LLM, enrichment, task queue, scoring."""

import gc
import json
import logging
import time

import config
from core.circuit_breaker import (
    _api_circuit_open, _api_record_failure, _api_record_success,
    pipeline_reset, is_pipeline_stopped,
)
from nlp.tfidf import extract_keywords
from apis.keyso import get_keyword_info, get_similar_keywords
from apis.google_trends import get_trends_for_keyword
from apis.llm import forecast_trend, suggest_keyso_queries
from storage.database import get_unprocessed_news, update_news_status, save_analysis

logger = logging.getLogger(__name__)

# Thresholds for full-auto pipeline (read from config)
FULL_AUTO_SCORE_THRESHOLD = config.FULL_AUTO_SCORE_THRESHOLD
FULL_AUTO_FINAL_THRESHOLD = config.FULL_AUTO_FINAL_THRESHOLD


# ─── Auto-review & scoring ───

def _auto_review_new():
    """Auto-review new news (free, local scoring only)."""
    try:
        from storage.database import get_connection, _is_postgres
        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"
            cur.execute(f"SELECT id, source, url, title, h1, description, plain_text, published_at, parsed_at, status FROM news WHERE status = 'new' ORDER BY parsed_at DESC LIMIT {ph}", (20,))
            if _is_postgres():
                columns = [desc[0] for desc in cur.description]
                news_list = [dict(zip(columns, row)) for row in cur.fetchall()]
            else:
                news_list = [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()

        if not news_list:
            return

        from checks.pipeline import run_review_pipeline
        result = run_review_pipeline(news_list, update_status=True)
        reviewed = len(result.get("results", []))
        dupes = sum(1 for r in result.get("results", []) if r.get("is_duplicate"))
        logger.info("Auto-review: %d checked, %d duplicates", reviewed, dupes)

        # Auto-export high-scoring news to NotReady tab in Sheets (score >= 60)
        AUTO_EXPORT_THRESHOLD = config.AUTO_EXPORT_THRESHOLD
        try:
            high_score_items = []
            news_by_id = {n.get("id", ""): n for n in news_list}
            for r in result.get("results", []):
                if (not r.get("is_duplicate") and not r.get("auto_rejected")
                        and r.get("total_score", 0) >= AUTO_EXPORT_THRESHOLD):
                    source_news = news_by_id.get(r.get("id", ""), {})
                    news_dict = {
                        "id": r.get("id", ""),
                        "title": source_news.get("title") or r.get("title", ""),
                        "source": source_news.get("source") or r.get("source", ""),
                        "url": source_news.get("url") or r.get("url", ""),
                        "h1": source_news.get("h1") or r.get("h1", ""),
                        "description": source_news.get("description") or r.get("description", ""),
                        "plain_text": source_news.get("plain_text") or r.get("plain_text", ""),
                        "published_at": source_news.get("published_at") or r.get("published_at", ""),
                        "parsed_at": source_news.get("parsed_at") or r.get("parsed_at", ""),
                    }
                    check_results = {
                        "checks": r.get("checks", {}),
                        "total_score": r.get("total_score", 0),
                        "sentiment": r.get("sentiment"),
                        "tags": r.get("tags"),
                        "headline": r.get("headline"),
                        "momentum": r.get("momentum"),
                        "entities": r.get("entities"),
                    }
                    high_score_items.append((news_dict, check_results))
            if high_score_items:
                from storage.sheets import write_not_ready_batch
                batch_result = write_not_ready_batch(high_score_items)
                written = batch_result.get("written", 0)
                skipped = batch_result.get("skipped", 0)
                logger.info("Auto-export to NotReady: %d written, %d skipped (threshold=%d)",
                            written, skipped, AUTO_EXPORT_THRESHOLD)
        except Exception as export_err:
            logger.warning("Auto-export to NotReady failed (non-fatal): %s", export_err)

        # Telegram notifications for high-scoring news
        try:
            if getattr(config, "TELEGRAM_BOT_TOKEN", ""):
                from bot.telegram_bot import notify_high_score, notify_pipeline_done
                high_score_news = []
                for r in result.get("results", []):
                    if not r.get("is_duplicate") and not r.get("auto_rejected"):
                        high_score_news.append({
                            "id": r.get("id", ""),
                            "title": r.get("title", ""),
                            "source": r.get("source", ""),
                            "total_score": r.get("total_score", 0),
                        })
                if high_score_news:
                    notify_high_score(high_score_news)
                notify_pipeline_done("auto_review", {
                    "reviewed": reviewed,
                    "duplicates": dupes,
                })
        except Exception as tg_err:
            logger.debug("Telegram notify skipped: %s", tg_err)

    except Exception as e:
        logger.error("Auto-review error: %s", e)


def _auto_rescore_zero():
    """Daily rescore of news with score=0 or missing analysis."""
    try:
        from storage.database import get_connection, _is_postgres
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT n.* FROM news n
                LEFT JOIN news_analysis a ON n.id = a.news_id
                WHERE n.status IN ('in_review', 'rejected')
                AND (a.total_score IS NULL OR a.total_score = 0 OR a.news_id IS NULL)
                AND n.plain_text != '' AND n.plain_text IS NOT NULL
                ORDER BY n.parsed_at DESC
                LIMIT 500
            """)
            if _is_postgres():
                columns = [desc[0] for desc in cur.description]
                news_list = [dict(zip(columns, row)) for row in cur.fetchall()]
            else:
                news_list = [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()

        if not news_list:
            return

        from checks.pipeline import run_review_pipeline
        result = run_review_pipeline(news_list, update_status=True)
        rescored = len(result.get("results", []))
        improved = sum(1 for r in result.get("results", []) if r.get("total_score", 0) > 0)
        logger.info("Auto-rescore: %d rescored, %d improved (score>0)", rescored, improved)

    except Exception as e:
        logger.error("Auto-rescore error: %s", e)


def _auto_approve_high_score(results: list):
    """Auto-approve high-scoring news and trigger enrichment."""
    import config
    threshold = getattr(config, "AUTO_APPROVE_THRESHOLD", 70)
    if threshold <= 0:
        return

    auto_ids = []
    for r in results:
        score = r.get("total_score", 0)
        is_dup = r.get("is_duplicate", False)
        is_rejected = r.get("auto_rejected", False)
        if score >= threshold and not is_dup and not is_rejected:
            auto_ids.append(r["id"])

    if not auto_ids:
        return

    from checks.pipeline import approve_for_enrichment
    approve_for_enrichment(auto_ids)
    logger.info("Auto-approved %d news (threshold=%d)", len(auto_ids), threshold)

    import threading
    def _bg_enrich(ids):
        for nid in ids:
            try:
                result = _process_single_news(nid)
                _auto_rewrite_if_recommended(nid, result)
            except Exception as e:
                logger.warning("Auto-enrich failed for %s: %s", nid, e)
    threading.Thread(target=_bg_enrich, args=(list(auto_ids),), daemon=True).start()


def _auto_rewrite_if_recommended(news_id: str, enrich_result: dict):
    """Auto-queue rewrite if LLM recommends publish_now."""
    import config
    if not getattr(config, "AUTO_REWRITE_ON_PUBLISH_NOW", True):
        return

    recommendation = enrich_result.get("recommendation", "")
    if recommendation != "publish_now":
        return

    style = getattr(config, "AUTO_REWRITE_STYLE", "news")

    import uuid
    from datetime import datetime, timezone
    from storage.database import get_connection, _is_postgres

    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    now = datetime.now(timezone.utc).isoformat()

    try:
        cur.execute(f"SELECT title FROM news WHERE id = {ph}", (news_id,))
        row = cur.fetchone()
        if not row:
            return
        title = row[0] if _is_postgres() else row["title"]
        tid = str(uuid.uuid4())[:12]
        cur.execute(f"""INSERT INTO task_queue (id, task_type, news_id, news_title, style, status, created_at, updated_at)
            VALUES ({','.join([ph]*8)})""",
            (tid, "rewrite", news_id, title[:200], style, "pending", now, now))
        if not _is_postgres():
            conn.commit()
        logger.info("Auto-queued rewrite for %s (publish_now)", news_id)
    except Exception as e:
        logger.warning("Auto-rewrite queue failed for %s: %s", news_id, e)
    finally:
        cur.close()

    try:
        _process_auto_rewrite(tid)
    except Exception as e:
        logger.warning("Auto-rewrite processing failed for %s: %s", tid, e)


# ─── Task queue helpers ───

def _update_task(task_id: str, status: str, result_data: dict | str | None = None):
    """Update task status in queue."""
    import json as _json
    from datetime import datetime, timezone
    from storage.database import get_connection, _is_postgres

    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    now = datetime.now(timezone.utc).isoformat()
    result_str = ""
    if result_data:
        result_str = _json.dumps(result_data, ensure_ascii=False) if isinstance(result_data, dict) else str(result_data)
    try:
        cur.execute(f"UPDATE task_queue SET status = {ph}, result = {ph}, updated_at = {ph} WHERE id = {ph}",
                    (status, result_str[:2000], now, task_id))
        if not _is_postgres():
            conn.commit()
    finally:
        cur.close()


def _create_task(task_type: str, news_id: str, news_title: str, style: str = "") -> str:
    """Create a task in the queue, return task_id."""
    import uuid
    from datetime import datetime, timezone
    from storage.database import get_connection, _is_postgres

    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    now = datetime.now(timezone.utc).isoformat()
    tid = str(uuid.uuid4())[:12]
    try:
        cur.execute(f"""INSERT INTO task_queue (id, task_type, news_id, news_title, style, status, created_at, updated_at)
            VALUES ({','.join([ph]*8)})""",
            (tid, task_type, news_id, news_title[:200], style, "pending", now, now))
        if not _is_postgres():
            conn.commit()
    finally:
        cur.close()
    return tid


def _fetch_news_by_id(news_id: str) -> dict | None:
    """Load news from DB by ID."""
    from storage.database import get_connection, _is_postgres
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"SELECT * FROM news WHERE id = {ph}", (news_id,))
        row = cur.fetchone()
        if not row:
            return None
        if _is_postgres():
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        return dict(row)
    finally:
        cur.close()


def _fetch_analysis_by_id(news_id: str) -> dict | None:
    """Load analysis from DB by news_id."""
    from storage.database import get_connection, _is_postgres
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"SELECT * FROM news_analysis WHERE news_id = {ph}", (news_id,))
        row = cur.fetchone()
        if not row:
            return None
        if _is_postgres():
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        return dict(row)
    finally:
        cur.close()


# ─── Enrichment / processing ───

def _process_single_news(news_id: str) -> dict:
    """Process a single news item by ID."""
    from storage.database import get_connection, _is_postgres
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        cur.execute(f"SELECT * FROM news WHERE id = {ph}", (news_id,))
        row = cur.fetchone()
        if not row:
            logger.warning("_process_single_news: news_id=%s not found", news_id)
            return {}
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            news = dict(zip(columns, row))
        else:
            news = dict(row)
    finally:
        cur.close()
    return _do_process(news)


def _do_process(news: dict) -> dict:
    """Full enrichment cycle for a single news item."""
    news_id = news["id"]
    title = news.get("title", "")
    text = news.get("plain_text", "") or news.get("description", "")

    # 1. TF-IDF
    combined_text = f"{title} {news.get('h1', '')} {text}"
    keywords = extract_keywords(combined_text)
    bigrams = keywords.get("bigrams", [])
    trigrams = keywords.get("trigrams", [])

    # 2. Keys.so (with rate limit + circuit breaker)
    top_bigram = bigrams[0][0] if bigrams else title
    source = news.get("source", "")
    keyso_region = config.keyso_region_for_source(source)
    from apis.cache import rate_check
    if rate_check("keyso") and not _api_circuit_open("keyso"):
        try:
            keyso_info = get_keyword_info(top_bigram, region=keyso_region)
            time.sleep(2)
            similar = get_similar_keywords(top_bigram, limit=10, region=keyso_region)
            time.sleep(2)
            _api_record_success("keyso")
        except Exception as e:
            logger.warning("Keys.so error: %s", e)
            _api_record_failure("keyso")
            keyso_info = {"ws": 0, "wsk": 0}
            similar = []
    else:
        logger.warning("Keys.so skipped (rate limit or circuit breaker)")
        keyso_info = {"ws": 0, "wsk": 0}
        similar = []

    # 3. Google Trends (with rate limit + circuit breaker)
    if rate_check("trends") and not _api_circuit_open("trends"):
        try:
            trends = get_trends_for_keyword(top_bigram)
            time.sleep(3)
            _api_record_success("trends")
        except Exception as e:
            logger.warning("Trends error: %s", e)
            _api_record_failure("trends")
            trends = {}
    else:
        logger.warning("Trends skipped (rate limit or circuit breaker)")
        trends = {}

    # 4. LLM (with rate limit + circuit breaker)
    if rate_check("llm") and not _api_circuit_open("llm"):
        try:
            fc = forecast_trend(
                title=title, text=text, bigrams=bigrams,
                keyso_freq=keyso_info.get("ws", 0), trends=trends,
            )
            time.sleep(2)
            _api_record_success("llm")
        except Exception as e:
            logger.warning("LLM error: %s", e)
            _api_record_failure("llm")
            fc = None
    else:
        logger.warning("LLM skipped (rate limit or circuit breaker)")
        fc = None
    recommendation = fc.get("recommendation", "") if fc else ""
    trend_score = str(fc.get("trend_score", "")) if fc else ""

    # 5. Save
    analysis_data = {
        "bigrams": bigrams, "trigrams": trigrams,
        "trends_data": trends,
        "keyso_data": {"freq": keyso_info.get("ws", 0), "similar": similar},
        "llm_recommendation": recommendation,
        "llm_trend_forecast": trend_score,
    }
    save_analysis(news_id, **analysis_data)

    update_news_status(news_id, "processed")
    logger.info("Processed: %s", title[:60])
    return {"trend_score": trend_score, "recommendation": recommendation, "bigrams": bigrams}


def process_news():
    """Process unprocessed news: NLP, APIs, LLM."""
    news_list = get_unprocessed_news(limit=10)
    if not news_list:
        logger.info("No unprocessed news")
        return

    for news in news_list:
        try:
            _do_process(news)
        except Exception as e:
            logger.error("Error processing news %s: %s", news.get("id"), e)


def _process_auto_rewrite(task_id: str):
    """Process a single rewrite task from queue."""
    import json as _json
    from apis.llm import rewrite_news
    from storage.database import get_connection, _is_postgres

    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"

    try:
        cur.execute(f"SELECT * FROM task_queue WHERE id = {ph}", (task_id,))
        task_row = cur.fetchone()
        if not task_row:
            logger.warning("_process_auto_rewrite: task_id=%s not found", task_id)
            return
        if _is_postgres():
            cols = [d[0] for d in cur.description]
            task = dict(zip(cols, task_row))
        else:
            task = dict(task_row)

        nid = task["news_id"]
        style = task.get("style", "news")

        cur.execute(f"SELECT * FROM news WHERE id = {ph}", (nid,))
        news_row = cur.fetchone()
        if not news_row:
            logger.warning("_process_auto_rewrite: news_id=%s not found for task %s", nid, task_id)
            return
        if _is_postgres():
            cols2 = [d[0] for d in cur.description]
            news = dict(zip(cols2, news_row))
        else:
            news = dict(news_row)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(f"UPDATE task_queue SET status = 'running', updated_at = {ph} WHERE id = {ph}", (now, task_id))
        if not _is_postgres():
            conn.commit()

        result = rewrite_news(
            title=news.get("title", ""),
            text=news.get("plain_text", ""),
            style=style,
            language="русский",
        )

        now = datetime.now(timezone.utc).isoformat()
        result_json = _json.dumps(result, ensure_ascii=False) if result else "{}"
        cur.execute(f"UPDATE task_queue SET status = 'done', result = {ph}, updated_at = {ph} WHERE id = {ph}",
                    (result_json, now, task_id))
        if not _is_postgres():
            conn.commit()
        logger.info("Auto-rewrite done for task %s", task_id)
    except Exception as e:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(f"UPDATE task_queue SET status = 'error', result = {ph}, updated_at = {ph} WHERE id = {ph}",
                    (str(e)[:500], now, task_id))
        if not _is_postgres():
            conn.commit()
        raise
    finally:
        cur.close()


# ─── Scoring ───

def _calc_final_score(analysis: dict) -> int:
    """Calculate final composite score (mirrors JS calcFinalScore).

    Веса берутся из config.SCORE_WEIGHT_* (по умолчанию internal 55% + viral 5% +
    keyso_bonus 15% + trends_bonus 10% + headline 15%). Фронтенд dashboard.html
    (calcFinalScore) должен держать те же значения.
    """
    import json as _json

    internal = float(analysis.get("total_score") or 0)
    viral = float(analysis.get("viral_score") or 0)
    headline = float(analysis.get("headline_score") or 0)

    # Keys.so bonus
    keyso_bonus = 0
    try:
        kd = analysis.get("keyso_data", "{}")
        if isinstance(kd, str):
            kd = _json.loads(kd) if kd else {}
        freq = float(kd.get("freq") or kd.get("ws") or 0)
        if freq >= 10000:
            keyso_bonus = 100
        elif freq >= 5000:
            keyso_bonus = 80
        elif freq >= 1000:
            keyso_bonus = 60
        elif freq >= 100:
            keyso_bonus = 40
        elif freq > 0:
            keyso_bonus = 20
    except Exception:
        logger.debug("keyso_bonus scoring failed, defaulting to 0", exc_info=True)

    # Trends bonus
    trends_bonus = 0
    try:
        td = analysis.get("trends_data", "{}")
        if isinstance(td, str):
            td = _json.loads(td) if td else {}
        vals = [float(v) for v in td.values() if str(v).replace(".", "").replace("-", "").isdigit()]
        max_t = max(vals) if vals else 0
        if max_t >= 80:
            trends_bonus = 100
        elif max_t >= 50:
            trends_bonus = 70
        elif max_t >= 20:
            trends_bonus = 40
        elif max_t > 0:
            trends_bonus = 20
    except Exception:
        logger.debug("trends_bonus scoring failed, defaulting to 0", exc_info=True)

    return round(
        internal * config.SCORE_WEIGHT_INTERNAL
        + viral * config.SCORE_WEIGHT_VIRAL
        + keyso_bonus * config.SCORE_WEIGHT_KEYSO
        + trends_bonus * config.SCORE_WEIGHT_TRENDS
        + headline * config.SCORE_WEIGHT_HEADLINE
    )


# ─── Sheets retry ───

SHEETS_RETRY_MAX_AGE_HOURS = 24  # Dead-letter after this many hours

def retry_sheets_exports():
    """Retry failed Sheets exports from task_queue. Dead-letter after 24h."""
    from datetime import datetime, timezone, timedelta
    from storage.database import db_cursor, rows_to_dicts
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT id, news_id, created_at FROM task_queue WHERE task_type='sheets_retry' AND status='pending'"
                " ORDER BY CASE WHEN style = 'urgent' THEN 0 ELSE 1 END, created_at ASC LIMIT 10"
            )
            tasks = rows_to_dicts(cur)

        if not tasks:
            return

        now = datetime.now(timezone.utc)
        deadline = now - timedelta(hours=SHEETS_RETRY_MAX_AGE_HOURS)

        for task in tasks:
            # Dead-letter: if task has been pending for over 24h, give up
            created_at = task.get("created_at", "")
            try:
                task_created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if task_created.tzinfo is None:
                    task_created = task_created.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, AttributeError):
                task_created = now  # can't parse — treat as fresh

            if task_created < deadline:
                logger.warning("Dead-letter: sheets export task %s failed after 24h, giving up", task["id"])
                _update_task(task["id"], "dead_letter", "Exceeded max retry period (24h)")
                continue

            news_id = task["news_id"]
            try:
                news = _fetch_news_by_id(news_id)
                if not news:
                    _update_task(task["id"], "completed", "News not found, skipping")
                    continue
                analysis = _fetch_analysis_by_id(news_id)
                from storage.sheets import write_ready_row
                # Sheets retry without rewrite — export the raw news row
                from storage.sheets import write_news_row
                write_news_row(news, analysis)
                _update_task(task["id"], "completed", "Retried successfully")
                logger.info("Sheets retry OK for %s", news_id)
            except Exception as e:
                logger.warning("Sheets retry failed for %s: %s", news_id, e)
    except Exception as e:
        logger.error("retry_sheets_exports error: %s", e)


# ─── Pipeline 1: Full Auto ───

def run_full_auto_pipeline(news_ids: list[str], task_ids: list[str]):
    """Mode 1: Full auto — score → enrich → rewrite → Sheets/Ready."""
    # LLM daily cost cap (opt-in via config.LLM_DAILY_CAP_USD; 0 = disabled).
    # Fail-open: any error in the check must NOT block the pipeline.
    try:
        cap = float(getattr(config, "LLM_DAILY_CAP_USD", 0) or 0)
        if cap > 0:
            from core.observability import get_cost_summary
            spent = float(get_cost_summary(days=1).get("total_cost_usd", 0) or 0)
            if spent >= cap:
                logger.warning("LLM daily cost cap reached: $%.2f >= $%.2f — skipping full-auto for %d news",
                               spent, cap, len(news_ids))
                for tid in task_ids:
                    _update_task(tid, "skipped", {"stage": "init", "reason": "llm_daily_cap", "cap_usd": cap})
                return
    except Exception:
        logger.debug("LLM cost cap check failed, proceeding", exc_info=True)
    pipeline_reset()
    from checks.pipeline import run_review_pipeline
    from apis.llm import rewrite_news
    from storage.sheets import write_ready_row

    def _trace(nid, step, decision, reason="", details=None, s_before=0, s_after=0):
        try:
            from core.observability import log_decision
            log_decision(nid, step, decision, reason, details, s_before, s_after)
        except Exception:
            logger.debug("log_decision trace failed for %s (%s)", nid, step, exc_info=True)

    for i, (news_id, task_id) in enumerate(zip(news_ids, task_ids)):
        if is_pipeline_stopped():
            for remaining_tid in task_ids[i:]:
                _update_task(remaining_tid, "cancelled", {"reason": "Остановлено пользователем"})
            logger.info("Full-auto pipeline stopped by user at %d/%d", i, len(news_ids))
            break

        try:
            news = _fetch_news_by_id(news_id)
            if not news:
                _update_task(task_id, "error", {"stage": "init", "error": "News not found"})
                continue

            # Stage 1: Local scoring
            _update_task(task_id, "running", {"stage": "scoring", "progress": f"{i+1}/{len(news_ids)}"})
            status = news.get("status", "new")
            analysis = _fetch_analysis_by_id(news_id)

            if analysis and analysis.get("total_score") is not None and status in ("in_review", "moderation"):
                total_score = analysis.get("total_score", 0)
                is_dup = status == "duplicate"
                is_rejected = status == "rejected" or total_score < config.AUTO_REJECT_SCORE_THRESHOLD
            else:
                review_result = run_review_pipeline([news], update_status=True)
                results = review_result.get("results", [])
                if not results:
                    _update_task(task_id, "error", {"stage": "scoring", "error": "No review results"})
                    continue
                check_result = results[0]
                total_score = check_result.get("total_score", 0)
                is_dup = check_result.get("is_duplicate", False)
                is_rejected = check_result.get("auto_rejected", False)

            if is_dup:
                _update_task(task_id, "skipped", {"stage": "scoring", "reason": "duplicate", "score": total_score})
                _trace(news_id, "full_auto", "skipped_duplicate", "Дубликат обнаружен", s_after=total_score)
                continue

            if is_rejected:
                _update_task(task_id, "skipped", {"stage": "scoring", "reason": "auto_rejected", "score": total_score})
                _trace(news_id, "full_auto", "auto_rejected", f"total_score={total_score} < {config.AUTO_REJECT_SCORE_THRESHOLD}", s_after=total_score)
                continue

            # Stage 2: Score threshold
            if total_score < FULL_AUTO_SCORE_THRESHOLD:
                _update_task(task_id, "skipped", {
                    "stage": "score_filter",
                    "reason": f"Скор {total_score} < {FULL_AUTO_SCORE_THRESHOLD}",
                    "score": total_score,
                })
                _trace(news_id, "full_auto", "skipped_low_score",
                       f"total_score={total_score} < порога {FULL_AUTO_SCORE_THRESHOLD}, не отправлен на LLM",
                       s_after=total_score)
                logger.info("Full-auto skip (score %d < %d): %s", total_score, FULL_AUTO_SCORE_THRESHOLD, news.get("title", "")[:50])
                continue

            # Stage 3: Enrichment
            _update_task(task_id, "running", {"stage": "enriching", "score": total_score})
            update_news_status(news_id, "approved")
            enrich_result = _do_process(news)
            recommendation = enrich_result.get("recommendation", "")

            # Stage 4: Final score
            analysis = _fetch_analysis_by_id(news_id)
            final_score = _calc_final_score(analysis) if analysis else 0

            _update_task(task_id, "running", {
                "stage": "final_score",
                "score": total_score,
                "final_score": final_score,
                "recommendation": recommendation,
            })

            if final_score < FULL_AUTO_FINAL_THRESHOLD:
                _update_task(task_id, "done", {
                    "stage": "filtered",
                    "reason": f"Финальный скор {final_score} < {FULL_AUTO_FINAL_THRESHOLD}",
                    "score": total_score,
                    "final_score": final_score,
                    "recommendation": recommendation,
                })
                _trace(news_id, "full_auto", "filtered_final_score",
                       f"final_score={final_score} < порога {FULL_AUTO_FINAL_THRESHOLD}, не отправлен на рерайт",
                       {"total_score": total_score, "final_score": final_score, "recommendation": recommendation},
                       s_before=total_score, s_after=final_score)
                logger.info("Full-auto filtered (final %d < %d): %s", final_score, FULL_AUTO_FINAL_THRESHOLD, news.get("title", "")[:50])
                continue

            # Stage 5: Rewrite
            _update_task(task_id, "running", {"stage": "rewriting", "score": total_score, "final_score": final_score})

            # Graceful degradation: if LLM circuit is open, skip rewrite and send raw to NotReady
            if _api_circuit_open("llm"):
                logger.warning("LLM circuit open — sending %s to NotReady without rewrite", news_id)
                try:
                    from storage.sheets import write_not_ready_row
                    # Build a minimal check_results dict from available analysis data
                    _check_res = {}
                    if analysis:
                        _check_res = {
                            "total_score": analysis.get("total_score", total_score),
                            "checks": {
                                "quality": {"score": analysis.get("quality_score", 0)},
                                "relevance": {"score": analysis.get("relevance_score", 0)},
                                "freshness": {"age_hours": -1},
                                "viral": {"score": analysis.get("viral_score", 0), "triggers": []},
                            },
                            "tags": analysis.get("tags", []),
                            "entities": [],
                            "sentiment": {"label": "neutral"},
                            "headline": {"score": 0},
                            "momentum": {"score": 0},
                        }
                    else:
                        _check_res = {"total_score": total_score, "checks": {}}
                    write_not_ready_row(news, _check_res)
                    update_news_status(news_id, "not_ready")
                    _update_task(task_id, "done", {
                        "stage": "not_ready_fallback",
                        "reason": "LLM circuit open — sent to NotReady raw",
                        "score": total_score,
                        "final_score": final_score,
                    })
                    _trace(news_id, "full_auto", "not_ready_llm_circuit_open",
                           "LLM недоступен (circuit breaker) — отправлен в NotReady без рерайта",
                           {"total_score": total_score, "final_score": final_score},
                           s_before=total_score, s_after=final_score)
                except Exception as e:
                    logger.error("NotReady fallback failed for %s: %s", news_id, e)
                    _update_task(task_id, "error", {"stage": "not_ready_fallback", "error": f"NotReady fallback failed: {e}"})
                continue  # skip to next news item

            style = getattr(config, "AUTO_REWRITE_STYLE", "news")
            rewrite = None
            for rewrite_attempt in range(2):
                try:
                    rewrite = rewrite_news(
                        title=news.get("title", ""),
                        text=news.get("plain_text", ""),
                        style=style,
                        language="русский",
                    )
                    if rewrite:
                        break
                except Exception as e:
                    logger.warning("Rewrite attempt %d/2 failed for %s: %s",
                                   rewrite_attempt + 1, news_id, e)
                    if rewrite_attempt == 0:
                        time.sleep(3)
            if not rewrite:
                _update_task(task_id, "error", {"stage": "rewriting", "error": "Rewrite returned None"})
                update_news_status(news_id, "in_review")
                continue

            _save_rewrite_article(news_id, news, rewrite, style)

            # Stage 6: Export to Sheets
            _update_task(task_id, "running", {"stage": "exporting", "score": total_score, "final_score": final_score})
            sheet_row = None
            try:
                sheet_row = write_ready_row(news, analysis, rewrite)
            except Exception as sheets_err:
                logger.warning("Sheets export failed for %s, queuing for retry: %s", news_id, sheets_err)
                try:
                    _create_task("sheets_retry", news_id, news.get("title", ""), "")
                except Exception:
                    logger.warning("Failed to queue sheets_retry task for %s — retry lost", news_id, exc_info=True)
                time.sleep(10)

            if sheet_row:
                update_news_status(news_id, "ready")
            else:
                logger.warning("Sheets export failed for %s, keeping status 'approved'", news_id)
            _update_task(task_id, "done", {
                "stage": "complete",
                "score": total_score,
                "final_score": final_score,
                "recommendation": recommendation,
                "sheet_row": sheet_row,
                "rewrite_title": rewrite.get("title", "")[:100],
            })
            _trace(news_id, "full_auto", "published_ready",
                   f"Прошёл все этапы: score={total_score}, final={final_score}, рерайт выполнен, экспорт в Sheets",
                   {"total_score": total_score, "final_score": final_score, "sheet_row": sheet_row},
                   s_before=total_score, s_after=final_score)
            logger.info("Full-auto complete: %s → final=%d, Ready row %s", news.get("title", "")[:50], final_score, sheet_row)

        except Exception as e:
            logger.error("Full-auto pipeline error for %s: %s", news_id, e)
            _update_task(task_id, "error", {"stage": "unknown", "error": str(e)[:500]})


def _save_rewrite_article(news_id: str, news: dict, rewrite: dict, style: str):
    """Save rewrite result as article in articles table."""
    import uuid
    from datetime import datetime, timezone
    from storage.database import get_connection, _is_postgres

    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    now = datetime.now(timezone.utc).isoformat()
    aid = str(uuid.uuid4())[:12]

    try:
        cur.execute(f"""INSERT INTO articles (id, news_id, title, text, seo_title, seo_description,
            feed_description, tags, style, language, original_title, original_text, source_url, status, created_at)
            VALUES ({','.join([ph]*15)})""", (
            aid, news_id,
            rewrite.get("title", "")[:500],
            rewrite.get("text", ""),
            rewrite.get("seo_title", "")[:500],
            rewrite.get("seo_description", "")[:1000],
            rewrite.get("feed_description", "")[:500],
            json.dumps(rewrite.get("tags", []), ensure_ascii=False),
            style, "русский",
            news.get("title", "")[:500],
            (news.get("plain_text", "") or "")[:3000],
            news.get("url", ""),
            "draft", now,
        ))
        if not _is_postgres():
            conn.commit()
        logger.info("Saved rewrite article %s for news %s", aid, news_id)
    except Exception as e:
        logger.warning("Failed to save rewrite article for %s: %s", news_id, e)
    finally:
        cur.close()


# ─── Pipeline 2: No LLM ───

def _build_check_result_from_analysis(analysis: dict) -> dict:
    """Build check_result from saved news_analysis (for already-scored items)."""
    import json as _json

    def _safe_loads(val, default):
        if val is None or val == "":
            return default
        if isinstance(val, (dict, list)):
            return val
        try:
            return _json.loads(val)
        except (ValueError, TypeError):
            return default

    viral_triggers = _safe_loads(analysis.get("viral_data"), [])

    checks = {
        "quality": {"score": analysis.get("quality_score", 0), "pass": True},
        "relevance": {"score": analysis.get("relevance_score", 0), "pass": True},
        "freshness": {
            "score": analysis.get("freshness_score", 0) if analysis.get("freshness_score") else 0,
            "pass": True,
            "age_hours": analysis.get("freshness_hours", -1),
            "status": analysis.get("freshness_status", ""),
        },
        "viral": {
            "score": analysis.get("viral_score", 0),
            "pass": True,
            "level": analysis.get("viral_level", ""),
            "triggers": viral_triggers if isinstance(viral_triggers, list) else [],
        },
    }

    tags = _safe_loads(analysis.get("tags_data") or analysis.get("tags"), [])
    sentiment = {"label": analysis.get("sentiment_label", "neutral") or "neutral", "score": 0}
    momentum = {"score": analysis.get("momentum_score", 0) or 0, "level": "none"}
    headline = {"score": analysis.get("headline_score", 0) or 0}
    entities = _safe_loads(analysis.get("entity_names") or analysis.get("entities"), [])

    return {
        "checks": checks,
        "tags": tags,
        "sentiment": sentiment,
        "momentum": momentum,
        "headline": headline,
        "entities": entities,
        "total_score": analysis.get("total_score", 0) or 0,
    }


def run_no_llm_pipeline(news_ids: list[str], task_ids: list[str]):
    """Mode 2: No LLM — score → Sheets/NotReady + moderation."""
    pipeline_reset()
    from checks.pipeline import run_review_pipeline
    from storage.sheets import write_not_ready_batch

    BATCH_SIZE = 25
    batch_items = []
    batch_task_ids = []
    batch_news_ids = []
    total_written = 0
    total_skipped = 0
    total_errors = 0

    def _flush_batch():
        nonlocal total_written, total_skipped, total_errors
        if not batch_items:
            return

        logger.info("No-LLM: flushing batch of %d items to Sheets...", len(batch_items))
        for tid in batch_task_ids:
            _update_task(tid, "running", {"stage": "exporting"})

        try:
            result = write_not_ready_batch(batch_items)
            written = result.get("written", 0)
            skipped = result.get("skipped", 0)
            errors = result.get("errors", 0)
            total_written += written
            total_skipped += skipped
            total_errors += errors

            for tid in batch_task_ids:
                _update_task(tid, "done", {
                    "stage": "complete",
                    "destination": "NotReady",
                    "batch_written": written,
                })

            for nid in batch_news_ids:
                update_news_status(nid, "moderation")

            logger.info("No-LLM batch flush: %d written, %d skipped, %d errors (total: %d/%d)",
                        written, skipped, errors, total_written, len(news_ids))

        except Exception as e:
            total_errors += len(batch_items)
            logger.error("No-LLM batch flush failed: %s", e)
            for tid in batch_task_ids:
                _update_task(tid, "error", {"stage": "exporting", "error": str(e)[:300]})

        batch_items.clear()
        batch_task_ids.clear()
        batch_news_ids.clear()

    for i, (news_id, task_id) in enumerate(zip(news_ids, task_ids)):
        if is_pipeline_stopped():
            _flush_batch()
            for remaining_tid in task_ids[i:]:
                _update_task(remaining_tid, "cancelled", {"reason": "Остановлено пользователем"})
            logger.info("No-LLM pipeline stopped by user at %d/%d", i, len(news_ids))
            break

        try:
            news = _fetch_news_by_id(news_id)
            if not news:
                _update_task(task_id, "error", {"stage": "init", "error": "News not found"})
                continue

            _update_task(task_id, "running", {"stage": "scoring", "progress": f"{i+1}/{len(news_ids)}"})

            status = news.get("status", "new")
            analysis = _fetch_analysis_by_id(news_id)

            if analysis and analysis.get("total_score") is not None and status in ("in_review", "moderation"):
                check_result = _build_check_result_from_analysis(analysis)
                total_score = check_result.get("total_score", 0)
                is_dup = status == "duplicate"
                is_rejected = status == "rejected"
            else:
                review_result = run_review_pipeline([news], update_status=True)
                results = review_result.get("results", [])
                if not results:
                    _update_task(task_id, "error", {"stage": "scoring", "error": "No review results"})
                    continue
                check_result = results[0]
                total_score = check_result.get("total_score", 0)
                is_dup = check_result.get("is_duplicate", False)
                is_rejected = check_result.get("auto_rejected", False)

            if is_dup:
                _update_task(task_id, "skipped", {"stage": "scoring", "reason": "duplicate", "score": total_score})
                continue

            if is_rejected:
                _update_task(task_id, "skipped", {"stage": "scoring", "reason": "auto_rejected", "score": total_score})
                continue

            batch_items.append((news, check_result))
            batch_task_ids.append(task_id)
            batch_news_ids.append(news_id)

            if len(batch_items) >= BATCH_SIZE:
                _flush_batch()

        except Exception as e:
            logger.error("No-LLM pipeline error for %s: %s", news_id, e)
            _update_task(task_id, "error", {"stage": "unknown", "error": str(e)[:500]})

    _flush_batch()

    logger.info("No-LLM pipeline complete: %d written, %d skipped, %d errors out of %d total",
                total_written, total_skipped, total_errors, len(news_ids))


# ─── Scheduled jobs ───

# NB: the daily digest published to Telegram is auto_publish_telegram_digest
# (config.AUTO_DIGEST_CRON_HOUR:00 MSK, default 20:00) → compose_digest("general",
# "day"), which records covered stories via record_digest_news for cross-day
# dedup. An older generate_auto_digest()
# (top-20 "brief", saved but never published, never scheduled) was removed as
# dead code — it duplicated the pull without recording history.

AUTO_TG_PUBLISH_MARKER = "auto_tg_publish_date"  # idempotency: date of last successful auto-publish (MSK)


def _today_msk():
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")


def auto_publish_telegram_digest():
    """Daily evening job: build the GENERAL digest (best of feed + cases + telegram
    over the last 24h, grouped into sections) and publish it to the Telegram channel.
    Idempotent — publishes at most once per MSK day (a date marker guards against a
    second send from the catch-up job / repeated restarts). Skips quietly if no fresh
    news or no channel configured."""
    try:
        from storage.database import get_app_setting, set_app_setting
        today = _today_msk()
        if get_app_setting(AUTO_TG_PUBLISH_MARKER) == today:
            logger.info("Auto TG digest: already published today (%s) — skip", today)
            return {"status": "already_done", "date": today}
        if not getattr(config, "TELEGRAM_BOT_TOKEN", "") or not getattr(config, "TELEGRAM_PUBLISH_CHANNEL", ""):
            logger.info("Auto TG digest: bot token/channel not set — skipping")
            return {"status": "no_token"}
        from api.news import compose_digest
        from apis.tg_publish import publish

        res = compose_digest("general", "day")
        digest = (res or {}).get("digest", {})
        if not digest or digest.get("news_count", 0) == 0:
            logger.info("Auto TG digest: no fresh news — nothing to publish")
            return {"status": "no_news"}

        title = (digest.get("title") or "").strip()
        text = digest.get("text") or ""
        body_text = (f"**\U0001F4E8 {title}**\n" if title else "") + text
        pub = publish({"text": body_text, "markdown": True})
        # Mark the day done only on a successful send, so failures retry on the next tick.
        if pub.get("status") == "ok":
            set_app_setting(AUTO_TG_PUBLISH_MARKER, today)
        logger.info("Auto TG general digest: %d items, publish status=%s parts=%s",
                    digest.get("news_count", 0), pub.get("status"), pub.get("parts"))
        return {"status": pub.get("status"), "parts": pub.get("parts"), "news_count": digest.get("news_count", 0), "title": title}
    except Exception as e:
        logger.error("Auto TG digest error: %s", e)
        return {"status": "error", "message": str(e)[:300]}


AUTO_TG_WEEKLY_MARKER = "auto_tg_weekly_date"  # идемпотентность: ISO-неделя последней публикации


def _isoweek_msk() -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%G-W%V")


def auto_publish_weekly_digest():
    """Воскресный вечерний недельный дайджест: general за 7 дней (лента+кейсы+
    телеграм + секция «Кого цитирует AI») → TG-канал. Идемпотентно по ISO-неделе —
    не больше одной публикации в неделю, что бы ни делали рестарты/catch-up."""
    try:
        from storage.database import get_app_setting, set_app_setting
        week = _isoweek_msk()
        if get_app_setting(AUTO_TG_WEEKLY_MARKER) == week:
            logger.info("Weekly TG digest: already published this week (%s) — skip", week)
            return {"status": "already_done", "week": week}
        if not getattr(config, "TELEGRAM_BOT_TOKEN", "") or not getattr(config, "TELEGRAM_PUBLISH_CHANNEL", ""):
            logger.info("Weekly TG digest: bot token/channel not set — skipping")
            return {"status": "no_token"}
        from api.news import compose_digest
        from apis.tg_publish import publish

        res = compose_digest("general", "week")
        digest = (res or {}).get("digest", {})
        if not digest or digest.get("news_count", 0) == 0:
            logger.info("Weekly TG digest: no news — nothing to publish")
            return {"status": "no_news"}

        title = (digest.get("title") or "").strip()
        text = digest.get("text") or ""
        body_text = (f"**\U0001F4C6 {title}**\n" if title else "") + text
        pub = publish({"text": body_text, "markdown": True})
        if pub.get("status") == "ok":
            set_app_setting(AUTO_TG_WEEKLY_MARKER, week)
        logger.info("Weekly TG digest: %d items, publish status=%s parts=%s",
                    digest.get("news_count", 0), pub.get("status"), pub.get("parts"))
        return {"status": pub.get("status"), "parts": pub.get("parts"),
                "news_count": digest.get("news_count", 0), "title": title}
    except Exception as e:
        logger.error("Weekly TG digest error: %s", e)
        return {"status": "error", "message": str(e)[:300]}


def catchup_weekly_digest():
    """Self-heal недельного: если воскресный слот пропущен (деплой в 19:00 вс),
    дошлёт в то же воскресенье после слота. No-op в остальные дни/до слота/после
    успешной публикации. Пропущенное воскресенье НЕ переносится на будни —
    «недельные итоги в среду» хуже, чем пропуск."""
    try:
        from datetime import datetime, timezone, timedelta
        msk = datetime.now(timezone.utc) + timedelta(hours=3)
        if msk.isoweekday() != 7 or msk.hour < config.WEEKLY_DIGEST_CRON_HOUR:
            return
        from storage.database import get_app_setting
        if get_app_setting(AUTO_TG_WEEKLY_MARKER) == _isoweek_msk():
            return
        logger.warning("Catch-up: weekly digest not sent yet (Sunday %s MSK) — publishing now",
                       msk.strftime("%H:%M"))
        auto_publish_weekly_digest()
    except Exception as e:
        logger.error("Catch-up weekly digest error: %s", e)


def catchup_tg_digest():
    """Self-heal: if the daily cron was missed (e.g. a deploy restarted the in-memory
    scheduler right at the slot), publish once it's past the configured hour (MSK) and
    not yet sent today. Runs on a short interval; a no-op before the slot or after the
    day is already sent. Same hour as the cron: config.AUTO_DIGEST_CRON_HOUR."""
    try:
        from datetime import datetime, timezone, timedelta
        slot_hour = config.AUTO_DIGEST_CRON_HOUR
        msk = datetime.now(timezone.utc) + timedelta(hours=3)
        if msk.hour < slot_hour:
            return  # before the slot — the daily cron will handle it
        from storage.database import get_app_setting
        if get_app_setting(AUTO_TG_PUBLISH_MARKER) == _today_msk():
            return  # already published today
        logger.warning("Catch-up: %02d:00 digest not sent yet (now %s MSK) — publishing now",
                       slot_hour, msk.strftime("%H:%M"))
        auto_publish_telegram_digest()
    except Exception as e:
        logger.error("Catch-up TG digest error: %s", e)


ALERT_CRITICAL_SOURCES = ["Google Search Status"]


def _esc_html(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_admin_alert(text):
    """Send an operational alert to the admin's private Telegram chat (HTML)."""
    chat = getattr(config, "TELEGRAM_ALERT_CHAT", "")
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not chat or not token:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=15,
        )
        return bool(r.ok)
    except Exception as e:
        logger.warning("Admin alert failed: %s", e)
        return False


def check_critical_alerts():
    """Scheduler job: alert the admin about new critical-source incidents (Core/Spam
    updates, outages) and mass source failures. Idempotent via app_settings markers,
    so restarts don't re-alert; pre-existing items are seeded silently."""
    try:
        from storage.database import get_app_setting, set_app_setting, get_connection, _is_postgres
        ph = "%s" if _is_postgres() else "?"
        conn = get_connection()
        cur = conn.cursor()
        try:
            for src in ALERT_CRITICAL_SOURCES:
                cur.execute(
                    f"SELECT title, url FROM news WHERE source = {ph} AND COALESCE(is_deleted, 0) = 0 "
                    f"ORDER BY COALESCE(published_ts, parsed_at) DESC LIMIT 1", (src,))
                row = cur.fetchone()
                if not row:
                    continue
                title, url = row[0], row[1]
                cur_val = (url or title or "").strip()
                if not cur_val:
                    continue
                key = "alert_last_" + src.replace(" ", "_")
                prev = get_app_setting(key)
                if not prev:
                    set_app_setting(key, cur_val)  # seed silently — don't alert pre-existing items
                elif prev != cur_val:
                    send_admin_alert(f"🚨 <b>{_esc_html(src)}</b>\n{_esc_html(title)}\n{url or ''}")
                    set_app_setting(key, cur_val)
        finally:
            cur.close()

        # Mass source failure (>= 5 auto-disabled at once), 6h cooldown to avoid spam.
        from core.source_health import source_health
        disabled = [n for n, s in source_health.get_status().items() if s.get("disabled_at")]
        if len(disabled) >= 5:
            import time
            last = get_app_setting("alert_mass_failure_ts")
            now = time.time()
            if not last or (now - float(last)) > 6 * 3600:
                send_admin_alert(
                    f"⚠️ <b>Массовый сбой источников</b>: {len(disabled)} отключено.\n"
                    "Примеры: " + _esc_html(", ".join(disabled[:8])))
                set_app_setting("alert_mass_failure_ts", str(now))
    except Exception as e:
        logger.error("check_critical_alerts error: %s", e)


PUBLISH_SPACING_MINUTES = config.PUBLISH_SPACING_MINUTES  # Minimum minutes between auto-publications


def publish_scheduled_articles():
    """Check scheduled articles and publish those whose time has come.

    Auto-spacing: if last publication was less than PUBLISH_SPACING_MINUTES ago,
    postpone remaining articles to maintain a steady feed.
    """
    try:
        from storage.database import get_connection, _is_postgres
        from datetime import datetime, timezone, timedelta

        conn = get_connection()
        cur = conn.cursor()
        ph = "%s" if _is_postgres() else "?"
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        try:
            cur.execute(
                f"SELECT id, title, scheduled_at FROM articles WHERE status = 'scheduled' AND scheduled_at <= {ph} ORDER BY scheduled_at",
                (now_iso,)
            )
            if _is_postgres():
                columns = [desc[0] for desc in cur.description]
                due_articles = [dict(zip(columns, row)) for row in cur.fetchall()]
            else:
                due_articles = [dict(row) for row in cur.fetchall()]

            if not due_articles:
                return

            # Find last published article time for spacing
            cur.execute(
                f"SELECT updated_at FROM articles WHERE status = 'published' ORDER BY updated_at DESC LIMIT 1"
            )
            last_row = cur.fetchone()
            if last_row:
                last_pub_str = last_row[0] if _is_postgres() else last_row["updated_at"]
                try:
                    last_pub = datetime.fromisoformat(last_pub_str.replace("Z", "+00:00"))
                    if last_pub.tzinfo is None:
                        last_pub = last_pub.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    last_pub = None
            else:
                last_pub = None

            published = 0
            for article in due_articles:
                aid = article["id"]

                # Auto-spacing: if last publication was too recent, reschedule
                if last_pub and (now - last_pub).total_seconds() < PUBLISH_SPACING_MINUTES * 60:
                    next_slot = last_pub + timedelta(minutes=PUBLISH_SPACING_MINUTES)
                    next_iso = next_slot.isoformat()
                    cur.execute(
                        f"UPDATE articles SET scheduled_at = {ph} WHERE id = {ph}",
                        (next_iso, aid)
                    )
                    logger.info("Auto-spacing: postponed %s to %s (+%d min)",
                                article.get("title", "")[:40], next_iso[:19], PUBLISH_SPACING_MINUTES)
                    last_pub = next_slot  # Next article will be spaced from this one
                    continue

                cur.execute(
                    f"UPDATE articles SET status = 'published', updated_at = {ph} WHERE id = {ph}",
                    (now_iso, aid)
                )
                logger.info("Auto-published scheduled article: %s", article.get("title", "")[:60])
                last_pub = now
                published += 1

            if not _is_postgres():
                conn.commit()

            if published:
                logger.info("Published %d scheduled articles", published)
        finally:
            cur.close()

    except Exception as e:
        logger.error("Scheduled publish error: %s", e)
