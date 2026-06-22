"""Extracted dashboard methods from web.py."""
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime as dt_mod, timezone, timedelta

from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def get_ops_dashboard():
    """Operational dashboard: action items, counts, health summary."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        result = {}

        # News counts by status
        cur.execute("SELECT status, COUNT(*) as cnt FROM news GROUP BY status")
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            status_rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            status_rows = [dict(r) for r in cur.fetchall()]
        status_counts = {r["status"]: r["cnt"] for r in status_rows}
        result["status_counts"] = status_counts

        # Pending review
        result["pending_review"] = status_counts.get("in_review", 0)

        # Ready to publish
        result["ready_to_publish"] = status_counts.get("ready", 0)

        # Articles in moderation
        result["in_moderation"] = status_counts.get("moderation", 0)

        # Queue stats
        cur.execute("SELECT status, COUNT(*) as cnt FROM task_queue GROUP BY status")
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            q_rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            q_rows = [dict(r) for r in cur.fetchall()]
        queue_counts = {r["status"]: r["cnt"] for r in q_rows}
        result["queue_counts"] = queue_counts
        result["queue_errors"] = queue_counts.get("error", 0)
        result["queue_running"] = queue_counts.get("running", 0) + queue_counts.get("pending", 0)

        # High-score candidates (final_score >= 60 not yet ready)
        ph = "%s" if _is_postgres() else "?"
        try:
            cur.execute(f"""
                SELECT COUNT(*) FROM news n
                JOIN news_analysis na ON n.id = na.news_id
                WHERE n.status IN ('processed', 'approved', 'in_review')
                AND na.total_score >= 60
            """)
            row = cur.fetchone()
            result["high_score_candidates"] = row[0] if row else 0
        except Exception:
            result["high_score_candidates"] = 0

        # Source health: count sources with recent failures
        try:
            from checks.health import get_source_health
            health_data = get_source_health()
            degraded = sum(1 for s in health_data if s.get("status") == "degraded" or s.get("error_rate", 0) > 0.3)
            result["degraded_sources"] = degraded
        except Exception:
            result["degraded_sources"] = 0

        # API cost today
        try:
            from core.observability import get_cost_summary
            cost = get_cost_summary(days=1)
            result["api_cost_today"] = cost.get("total_cost_usd", 0)
            result["api_calls_today"] = cost.get("total_calls", 0)
        except Exception:
            result["api_cost_today"] = 0
            result["api_calls_today"] = 0

        # Draft articles count
        try:
            cur.execute("SELECT COUNT(*) FROM articles WHERE status = 'draft'")
            row = cur.fetchone()
            result["draft_articles"] = row[0] if row else 0
        except Exception:
            result["draft_articles"] = 0

        # Action items (prioritized recommendations)
        actions = []
        if result["pending_review"] > 0:
            actions.append({
                "priority": 1,
                "type": "review",
                "title": f"Проверь {result['pending_review']} новост{'ь' if result['pending_review'] == 1 else 'ей'} на модерации",
                "tab": "editorial",
                "count": result["pending_review"],
            })
        if result["queue_errors"] > 0:
            actions.append({
                "priority": 2,
                "type": "error",
                "title": f"{result['queue_errors']} задач{'а' if result['queue_errors'] == 1 else ''} с ошибкой в очереди",
                "tab": "queue",
                "count": result["queue_errors"],
            })
        if result["high_score_candidates"] > 0:
            actions.append({
                "priority": 3,
                "type": "opportunity",
                "title": f"{result['high_score_candidates']} кандидат{'ов' if result['high_score_candidates'] != 1 else ''} с высоким скором",
                "tab": "final",
                "count": result["high_score_candidates"],
            })
        if result["degraded_sources"] > 0:
            actions.append({
                "priority": 4,
                "type": "warning",
                "title": f"{result['degraded_sources']} источник{'ов' if result['degraded_sources'] != 1 else ''} деградируют",
                "tab": "health",
                "count": result["degraded_sources"],
            })
        if result["ready_to_publish"] > 0:
            actions.append({
                "priority": 5,
                "type": "publish",
                "title": f"{result['ready_to_publish']} материал{'ов' if result['ready_to_publish'] != 1 else ''} готовы к публикации",
                "tab": "editor",
                "count": result["ready_to_publish"],
            })
        if result["draft_articles"] > 0:
            actions.append({
                "priority": 6,
                "type": "draft",
                "title": f"{result['draft_articles']} черновик{'ов' if result['draft_articles'] != 1 else ''} ждут доработки",
                "tab": "editor",
                "count": result["draft_articles"],
            })

        result["actions"] = sorted(actions, key=lambda a: a["priority"])
        return result
    except Exception as e:
        logger.error("Ops dashboard error: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        cur.close()


def get_storylines(days: int = 3):
    """Return clustered news storylines from last N days."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cutoff = (dt_mod.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur.execute(f"""
            SELECT n.id, n.source, n.title, n.url, n.published_at, n.status,
                   COALESCE(a.total_score, 0) as total_score,
                   COALESCE(a.viral_score, 0) as viral_score,
                   COALESCE(a.entity_names, '[]') as entity_names,
                   COALESCE(a.viral_data, '{{}}') as viral_data
            FROM news n
            LEFT JOIN news_analysis a ON n.id = a.news_id
            WHERE n.parsed_at > {ph}
            ORDER BY n.published_at DESC
            LIMIT 500
        """, (cutoff,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            news_list = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            news_list = [dict(r) for r in cur.fetchall()]

        if len(news_list) < 2:
            return {"storylines": [], "total_news": len(news_list)}

        from checks.deduplication import tfidf_similarity, build_groups
        titles = [n["title"] for n in news_list]
        pairs = tfidf_similarity(titles)
        groups = build_groups(news_list, pairs)

        # Deduplication: each news appears in only one storyline (the largest)
        used_ids = set()
        storylines = []
        # Sort groups by size descending so largest clusters claim members first
        groups.sort(key=lambda g: -len(g["members"]))
        for g in groups:
            members = [m for m in g["members"] if m.get("id") not in used_ids]
            if len(members) < 2:
                continue
            for m in members:
                used_ids.add(m.get("id"))
            sources = list(set(m.get("source", "") for m in members))
            avg_score = round(sum(m.get("total_score", 0) for m in members) / len(members)) if members else 0
            max_viral = max((m.get("viral_score", 0) for m in members), default=0)
            count = len(members)
            phase = "trending" if count >= 5 else "developing" if count >= 3 else "emerging"

            # Aggregate game entities across cluster
            all_entities = []
            for m in members:
                try:
                    ents = json.loads(m.get("entity_names") or "[]")
                    if isinstance(ents, list):
                        all_entities.extend(ents)
                except Exception:
                    pass
            ent_counts = Counter(all_entities)
            top_games = [name for name, _ in ent_counts.most_common(10)]

            # Aggregate viral triggers across cluster
            all_triggers = []
            for m in members:
                try:
                    vd = m.get("viral_data") or "{}"
                    vdata = json.loads(vd) if isinstance(vd, str) else vd
                    if isinstance(vdata, list):
                        all_triggers.extend(vdata)
                    elif isinstance(vdata, dict):
                        all_triggers.extend(vdata.get("triggers", []))
                except Exception:
                    pass
            trig_counts = Counter()
            for t in all_triggers:
                label = t.get("label") or t.get("trigger") or t.get("name") or (t if isinstance(t, str) else "")
                if label:
                    trig_counts[label] += 1
            top_triggers = [name for name, _ in trig_counts.most_common(5)]

            storylines.append({
                "count": count,
                "phase": phase,
                "status": g["status"],
                "sources": sources,
                "avg_score": avg_score,
                "max_viral": max_viral,
                "top_games": top_games,
                "top_triggers": top_triggers,
                "members": [{
                    "id": m.get("id", ""),
                    "title": m.get("title", ""),
                    "source": m.get("source", ""),
                    "url": m.get("url", ""),
                    "published_at": m.get("published_at", ""),
                    "status": m.get("status", ""),
                    "total_score": m.get("total_score", 0),
                } for m in members[:10]],
                "duplicate_indices": g.get("duplicate_indices", []),
            })

        storylines.sort(key=lambda s: (-s["count"], -s["avg_score"]))
        return {"storylines": storylines[:50], "total_news": len(news_list)}
    except Exception as e:
        logger.error("Storylines error: %s", e)
        return {"status": "error", "message": str(e), "storylines": []}
    finally:
        cur.close()


def get_source_health_plus():
    """Enhanced source health: 7-day trend, score stats, conversion, recommendations."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        from checks.health import get_sources_health
        base_health = get_sources_health()

        cutoff_7d = (dt_mod.now(timezone.utc) - timedelta(days=7)).isoformat()
        day_expr = "CAST(parsed_at AS TEXT)" if _is_postgres() else "parsed_at"
        cur.execute(f"""
            SELECT source,
                   SUBSTRING({day_expr}, 1, 10) as day,
                   COUNT(*) as cnt
            FROM news
            WHERE parsed_at > {ph}
            GROUP BY source, SUBSTRING({day_expr}, 1, 10)
            ORDER BY source, day
        """, (cutoff_7d,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            trend_rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            trend_rows = [dict(r) for r in cur.fetchall()]

        trend_data = defaultdict(dict)
        for r in trend_rows:
            trend_data[r["source"]][r["day"]] = r["cnt"]

        days_list = [(dt_mod.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

        cur.execute(f"""
            SELECT n.source,
                   COUNT(*) as total,
                   COALESCE(AVG(a.total_score), 0) as avg_score,
                   COALESCE(MAX(a.total_score), 0) as max_score,
                   SUM(CASE WHEN n.status IN ('ready', 'processed', 'approved') THEN 1 ELSE 0 END) as good_count,
                   SUM(CASE WHEN n.status IN ('rejected', 'duplicate') THEN 1 ELSE 0 END) as bad_count
            FROM news n
            LEFT JOIN news_analysis a ON n.id = a.news_id
            WHERE n.parsed_at > {ph}
            GROUP BY n.source
        """, (cutoff_7d,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            score_rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            score_rows = [dict(r) for r in cur.fetchall()]
        score_map = {r["source"]: r for r in score_rows}

        results = []
        for h in base_health:
            src = h["source"]
            trend = [trend_data.get(src, {}).get(d, 0) for d in days_list]
            stats = score_map.get(src, {})
            total = stats.get("total", 0)
            good = stats.get("good_count", 0)
            bad = stats.get("bad_count", 0)
            conversion = round(good / total * 100) if total > 0 else 0

            recs = []
            if h["status"] in ("dead", "down"):
                recs.append({"type": "error", "text": "Проверьте RSS/URL — источник не отвечает"})
            elif h["status"] == "warning":
                recs.append({"type": "warning", "text": "Источник нестабилен, возможны проблемы с доступом"})
            if total > 10 and conversion < 10:
                recs.append({"type": "warning", "text": f"Низкая конверсия ({conversion}%) — рассмотрите снижение веса"})
            avg_score = round(float(stats.get("avg_score", 0)))
            if total > 10 and avg_score < 20:
                recs.append({"type": "info", "text": f"Средний скор {avg_score} — контент низкого качества"})
            if sum(trend[-3:]) == 0 and h["status"] != "dead":
                recs.append({"type": "warning", "text": "Нет статей за последние 3 дня"})
            trend_direction = "stable"
            if len(trend) >= 4:
                first_half = sum(trend[:3])
                second_half = sum(trend[4:])
                if second_half > first_half * 1.5:
                    trend_direction = "up"
                elif second_half < first_half * 0.5:
                    trend_direction = "down"

            results.append({
                **h,
                "trend_7d": trend,
                "trend_days": days_list,
                "trend_direction": trend_direction,
                "avg_score": avg_score,
                "max_score": stats.get("max_score", 0),
                "total_7d": total,
                "good_count": good,
                "bad_count": bad,
                "conversion_pct": conversion,
                "recommendations": recs,
            })

        results.sort(key=lambda x: (0 if x["recommendations"] else 1, -x.get("count_24h", 0)))
        return {"sources": results, "days": days_list}
    except Exception as e:
        logger.error("Source health plus error: %s", e)
        return {"status": "error", "message": str(e), "sources": []}
    finally:
        cur.close()


def simulate_thresholds(body):
    """Simulate how many articles would pass at given thresholds."""
    score_min = int(body.get("score_min", 0))
    score_max = int(body.get("score_max", 100))
    final_min = int(body.get("final_min", 0))
    final_max = int(body.get("final_max", 100))
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cutoff = (dt_mod.now(timezone.utc) - timedelta(days=7)).isoformat()
        cur.execute(f"""
            SELECT n.source, n.status,
                   COALESCE(a.total_score, 0) as total_score,
                   COALESCE(a.viral_score, 0) as viral_score,
                   COALESCE(a.final_score, 0) as final_score
            FROM news n
            LEFT JOIN news_analysis a ON n.id = a.news_id
            WHERE n.parsed_at > {ph} AND a.total_score > 0
            ORDER BY a.total_score DESC
        """, (cutoff,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            rows = [dict(r) for r in cur.fetchall()]

        total = len(rows)
        pass_score = sum(1 for r in rows if score_min <= r["total_score"] <= score_max)
        pass_final = sum(1 for r in rows if final_min <= (r.get("final_score") or 0) <= final_max)
        pass_both = sum(1 for r in rows if score_min <= r["total_score"] <= score_max and final_min <= (r.get("final_score") or 0) <= final_max)

        buckets = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
        for r in rows:
            s = r["total_score"]
            if s < 20: buckets["0-19"] += 1
            elif s < 40: buckets["20-39"] += 1
            elif s < 60: buckets["40-59"] += 1
            elif s < 80: buckets["60-79"] += 1
            else: buckets["80-100"] += 1

        final_buckets = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
        for r in rows:
            s = r.get("final_score") or 0
            if s < 20: final_buckets["0-19"] += 1
            elif s < 40: final_buckets["20-39"] += 1
            elif s < 60: final_buckets["40-59"] += 1
            elif s < 80: final_buckets["60-79"] += 1
            else: final_buckets["80-100"] += 1

        by_source = defaultdict(lambda: {"total": 0, "pass_score": 0, "pass_final": 0})
        for r in rows:
            src = r["source"]
            by_source[src]["total"] += 1
            if score_min <= r["total_score"] <= score_max:
                by_source[src]["pass_score"] += 1
            if final_min <= (r.get("final_score") or 0) <= final_max:
                by_source[src]["pass_final"] += 1

        return {
            "total": total,
            "pass_score": pass_score,
            "pass_final": pass_final,
            "pass_both": pass_both,
            "pct_score": round(pass_score / total * 100) if total > 0 else 0,
            "pct_final": round(pass_final / total * 100) if total > 0 else 0,
            "score_distribution": buckets,
            "final_distribution": final_buckets,
            "by_source": dict(by_source),
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "total": 0}
    finally:
        cur.close()


def export_storylines_to_sheets(days: int = 3, trigger: str = "manual"):
    """Fetch current storylines and export them to Google Sheets 'Сюжеты' tab."""
    data = get_storylines(days=days)
    storylines = data.get("storylines", [])
    if not storylines:
        return {"status": "error", "message": "Нет сюжетов для экспорта (нужно минимум 2 связанные новости)"}

    from storage.sheets import write_storylines
    result = write_storylines(storylines)
    result["total_storylines"] = len(storylines)
    result["total_news"] = data.get("total_news", 0)

    # Log export to DB for tracking
    _log_storylines_export(result, days, trigger)
    return result


def _log_storylines_export(result: dict, days: int, trigger: str):
    """Save export record to decision_trace for history tracking."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        ph = "%s" if _is_postgres() else "?"
        import uuid, json
        now = dt_mod.now(timezone.utc).isoformat()
        details = json.dumps({
            "written": result.get("written", 0),
            "storylines": result.get("total_storylines", 0),
            "news": result.get("total_news", 0),
            "days": days,
            "status": result.get("status", ""),
        }, ensure_ascii=False)
        try:
            cur.execute(f"""INSERT INTO decision_trace (id, news_id, step, decision, reason, details, correlation_id, created_at)
                VALUES ({','.join([ph]*8)})""",
                (str(uuid.uuid4())[:12], "", "storylines_export", result.get("status", "error"),
                 f"{trigger}: {result.get('total_storylines', 0)} сюжетов, {result.get('written', 0)} строк",
                 details, trigger, now))
            if not _is_postgres():
                conn.commit()
        finally:
            cur.close()
    except Exception as e:
        logger.debug("Storylines export log failed: %s", e)


def get_storylines_export_history(limit: int = 20) -> list[dict]:
    """Get recent storylines export history from decision_trace."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"""SELECT id, decision, reason, details, correlation_id as trigger, created_at
            FROM decision_trace WHERE step = 'storylines_export'
            ORDER BY created_at DESC LIMIT {ph}""", (limit,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, r)) for r in cur.fetchall()]
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        cur.close()


def get_storylines_settings() -> dict:
    """Get storylines auto-export settings from DB."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    defaults = {"enabled": False, "hour": 9, "minute": 0, "days": 3}
    try:
        cur.execute(f"SELECT flag_id, description FROM feature_flags WHERE flag_id = {ph}",
                    ("storylines_auto_export",))
        row = cur.fetchone()
        if row:
            import json
            val = row[1] if _is_postgres() else row["description"]
            try:
                settings = json.loads(val)
                return {**defaults, **settings}
            except (json.JSONDecodeError, TypeError):
                pass
        return defaults
    except Exception:
        return defaults
    finally:
        cur.close()


def save_storylines_settings(body: dict) -> dict:
    """Save storylines auto-export settings to DB and update scheduler."""
    import json
    enabled = bool(body.get("enabled", False))
    hour = int(body.get("hour", 9))
    minute = int(body.get("minute", 0))
    days = int(body.get("days", 3))

    if not (0 <= hour <= 23):
        return {"status": "error", "message": "Час должен быть 0-23"}
    if not (0 <= minute <= 59):
        return {"status": "error", "message": "Минуты должны быть 0-59"}
    if not (1 <= days <= 14):
        return {"status": "error", "message": "Дни должны быть 1-14"}

    settings = {"enabled": enabled, "hour": hour, "minute": minute, "days": days}
    settings_json = json.dumps(settings)

    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        if _is_postgres():
            cur.execute(f"""INSERT INTO feature_flags (flag_id, enabled, description)
                VALUES ({ph}, {ph}, {ph})
                ON CONFLICT (flag_id) DO UPDATE SET enabled = EXCLUDED.enabled, description = EXCLUDED.description""",
                ("storylines_auto_export", 1 if enabled else 0, settings_json))
        else:
            cur.execute(f"INSERT OR REPLACE INTO feature_flags (flag_id, enabled, description) VALUES ({ph}, {ph}, {ph})",
                ("storylines_auto_export", 1 if enabled else 0, settings_json))
            conn.commit()

        # Update scheduler job
        _update_storylines_cron(enabled, hour, minute, days)

        return {"status": "ok", "settings": settings}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        cur.close()


def _update_storylines_cron(enabled: bool, hour: int, minute: int, days: int):
    """Add/remove/update the storylines auto-export cron job in APScheduler."""
    try:
        import scheduler as sched_module
        val = getattr(sched_module, "RUNNING_SCHEDULER", None)
        if val is not None:
            _do_update_cron(val, enabled, hour, minute, days)
            return
    except Exception as e:
        logger.debug("Storylines cron update skipped (scheduler not running): %s", e)


def _do_update_cron(scheduler, enabled: bool, hour: int, minute: int, days: int):
    """Internal: update cron job on a running scheduler."""
    job_id = "storylines_auto_export"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    if enabled:
        def _auto_export():
            logger.info("Storylines auto-export triggered (days=%d)", days)
            export_storylines_to_sheets(days=days, trigger="auto")

        scheduler.add_job(_auto_export, "cron", hour=hour, minute=minute,
                          id=job_id, replace_existing=True)
        # Remove hardcoded scheduler.py cron to avoid duplicate exports
        try:
            scheduler.remove_job("storylines_daily_export_9msk")
        except Exception:
            pass
        logger.info("Storylines auto-export scheduled: %02d:%02d daily, %d days", hour, minute, days)
