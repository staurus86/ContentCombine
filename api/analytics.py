"""Extracted analytics methods from web.py."""
import json
import logging
from datetime import datetime as dt_mod, timezone, timedelta

from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def get_analytics():
    """Возвращает аналитику для дашборда."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"

        # 1. Top sources (7 days)
        if _is_postgres():
            cur.execute("""SELECT source, COUNT(*) as cnt FROM news
                WHERE parsed_at::timestamptz > (NOW() - INTERVAL '7 days') GROUP BY source ORDER BY cnt DESC LIMIT 15""")
        else:
            cur.execute("SELECT source, COUNT(*) as cnt FROM news WHERE parsed_at > datetime('now', '-7 days') GROUP BY source ORDER BY cnt DESC LIMIT 15")
        top_sources = []
        for row in cur.fetchall():
            if _is_postgres():
                top_sources.append({"source": row[0], "count": row[1]})
            else:
                top_sources.append({"source": row["source"], "count": row["cnt"]})

        # 2. Status distribution
        cur.execute("SELECT status, COUNT(*) as cnt FROM news GROUP BY status")
        statuses = {}
        for row in cur.fetchall():
            if _is_postgres():
                statuses[row[0]] = row[1]
            else:
                statuses[row["status"]] = row["cnt"]

        # 3. Approval rate
        total_decisions = statuses.get("approved", 0) + statuses.get("processed", 0) + statuses.get("rejected", 0) + statuses.get("duplicate", 0)
        approved_total = statuses.get("approved", 0) + statuses.get("processed", 0)
        approval_rate = round(approved_total / total_decisions * 100, 1) if total_decisions > 0 else 0

        # 4. Top viral triggers (from review results in last 7 days of news_analysis)
        if _is_postgres():
            cur.execute("SELECT bigrams FROM news_analysis WHERE processed_at > (NOW() - INTERVAL '7 days')::text LIMIT 500")
        else:
            cur.execute("SELECT bigrams FROM news_analysis WHERE processed_at > datetime('now', '-7 days') LIMIT 500")
        all_bigrams = {}
        for row in cur.fetchall():
            raw = row[0] if _is_postgres() else row["bigrams"]
            try:
                for bg in json.loads(raw or "[]"):
                    term = bg[0] if isinstance(bg, list) else bg
                    all_bigrams[term] = all_bigrams.get(term, 0) + 1
            except Exception:
                pass
        top_bigrams = sorted(all_bigrams.items(), key=lambda x: x[1], reverse=True)[:20]

        # 5. News per day (last 14 days)
        if _is_postgres():
            cur.execute("""SELECT DATE(parsed_at::timestamp) as d, COUNT(*) as cnt FROM news
                WHERE parsed_at::timestamptz > (NOW() - INTERVAL '14 days') GROUP BY d ORDER BY d""")
        else:
            cur.execute("SELECT DATE(parsed_at) as d, COUNT(*) as cnt FROM news WHERE parsed_at > datetime('now', '-14 days') GROUP BY d ORDER BY d")
        daily = []
        for row in cur.fetchall():
            if _is_postgres():
                daily.append({"date": str(row[0]), "count": row[1]})
            else:
                daily.append({"date": row["d"], "count": row["cnt"]})

        # 6. Peak hours
        if _is_postgres():
            cur.execute("""SELECT EXTRACT(HOUR FROM parsed_at::timestamp)::int as h, COUNT(*) as cnt FROM news
                WHERE parsed_at::timestamptz > (NOW() - INTERVAL '7 days') GROUP BY h ORDER BY cnt DESC""")
        else:
            cur.execute("SELECT CAST(strftime('%H', parsed_at) AS INTEGER) as h, COUNT(*) as cnt FROM news WHERE parsed_at > datetime('now', '-7 days') GROUP BY h ORDER BY cnt DESC")
        peak_hours = []
        for row in cur.fetchall():
            if _is_postgres():
                peak_hours.append({"hour": row[0], "count": row[1]})
            else:
                peak_hours.append({"hour": row["h"], "count": row["cnt"]})

        # 7. Source weights
        try:
            from checks.source_weight import get_source_stats
            source_stats = get_source_stats()
        except Exception:
            source_stats = []

        # 8. Feedback summary
        try:
            from checks.feedback import get_feedback_summary
            feedback = get_feedback_summary()
        except Exception:
            feedback = {"sources": [], "tags": []}

        # 9. Articles stats
        cur.execute("SELECT status, COUNT(*) as cnt FROM articles GROUP BY status")
        art_stats = {}
        for row in cur.fetchall():
            if _is_postgres():
                art_stats[row[0]] = row[1]
            else:
                art_stats[row["status"]] = row["cnt"]

        # 10. Avg score per day (14 days)
        if _is_postgres():
            cur.execute("""SELECT DATE(n.parsed_at::timestamp) as d,
                ROUND(AVG(COALESCE(a.total_score,0))::numeric, 1) as avg_score,
                COUNT(*) as cnt
                FROM news n LEFT JOIN news_analysis a ON n.id = a.news_id
                WHERE n.parsed_at::timestamptz > (NOW() - INTERVAL '14 days') AND a.total_score > 0
                GROUP BY d ORDER BY d""")
        else:
            cur.execute("""SELECT DATE(n.parsed_at) as d,
                ROUND(AVG(COALESCE(a.total_score,0)), 1) as avg_score,
                COUNT(*) as cnt
                FROM news n LEFT JOIN news_analysis a ON n.id = a.news_id
                WHERE n.parsed_at > datetime('now', '-14 days') AND a.total_score > 0
                GROUP BY d ORDER BY d""")
        score_trend = []
        for row in cur.fetchall():
            if _is_postgres():
                score_trend.append({"date": str(row[0]), "avg_score": float(row[1]), "count": row[2]})
            else:
                score_trend.append({"date": row["d"], "avg_score": float(row["avg_score"]), "count": row["cnt"]})

        # 11. Conversion per day (approved vs rejected, 14 days)
        if _is_postgres():
            cur.execute("""SELECT DATE(parsed_at::timestamp) as d, status, COUNT(*) as cnt FROM news
                WHERE parsed_at::timestamptz > (NOW() - INTERVAL '14 days')
                AND status IN ('approved','processed','ready','rejected','duplicate')
                GROUP BY d, status ORDER BY d""")
        else:
            cur.execute("""SELECT DATE(parsed_at) as d, status, COUNT(*) as cnt FROM news
                WHERE parsed_at > datetime('now', '-14 days')
                AND status IN ('approved','processed','ready','rejected','duplicate')
                GROUP BY d, status ORDER BY d""")
        conv_raw = {}
        for row in cur.fetchall():
            d = str(row[0]) if _is_postgres() else row["d"]
            st = row[1] if _is_postgres() else row["status"]
            cnt = row[2] if _is_postgres() else row["cnt"]
            if d not in conv_raw:
                conv_raw[d] = {"date": d, "approved": 0, "rejected": 0}
            if st in ("approved", "processed", "ready"):
                conv_raw[d]["approved"] += cnt
            elif st in ("rejected", "duplicate"):
                conv_raw[d]["rejected"] += cnt
        conversion_daily = sorted(conv_raw.values(), key=lambda x: x["date"])

        return {
            "status": "ok",
            "top_sources": top_sources,
            "statuses": statuses,
            "approval_rate": approval_rate,
            "top_bigrams": top_bigrams,
            "daily": daily,
            "peak_hours": peak_hours[:5],
            "source_stats": source_stats,
            "feedback": feedback,
            "article_stats": art_stats,
            "total_news": sum(statuses.values()),
            "total_articles": sum(art_stats.values()),
            "score_trend": score_trend,
            "conversion_daily": conversion_daily,
        }

    finally:
        cur.close()


def get_funnel_analytics():
    """Full pipeline funnel: parsed -> reviewed -> approved -> enriched -> final_passed -> rewritten -> exported -> published."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        funnel = {}

        # Total parsed
        cur.execute("SELECT COUNT(*) FROM news")
        funnel["parsed"] = cur.fetchone()[0]

        # Reviewed (has analysis)
        cur.execute("SELECT COUNT(*) FROM news_analysis WHERE total_score > 0")
        funnel["reviewed"] = cur.fetchone()[0]

        # By status
        for status in ["in_review", "approved", "processed", "moderation", "ready", "rejected", "duplicate"]:
            cur.execute(f"SELECT COUNT(*) FROM news WHERE status = {ph}", (status,))
            funnel[status] = cur.fetchone()[0]

        # Articles created (rewritten)
        cur.execute("SELECT COUNT(*) FROM articles")
        funnel["rewritten"] = cur.fetchone()[0]

        # Published articles
        cur.execute(f"SELECT COUNT(*) FROM articles WHERE status = 'published'")
        funnel["published"] = cur.fetchone()[0]

        # Conversion by source
        cur.execute("""
            SELECT n.source,
                   COUNT(*) as total,
                   SUM(CASE WHEN n.status = 'ready' THEN 1 ELSE 0 END) as ready_count,
                   SUM(CASE WHEN n.status = 'rejected' THEN 1 ELSE 0 END) as rejected_count,
                   SUM(CASE WHEN n.status = 'duplicate' THEN 1 ELSE 0 END) as dup_count
            FROM news n
            GROUP BY n.source
            ORDER BY total DESC
        """)
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            by_source = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            by_source = [dict(r) for r in cur.fetchall()]

        # Score distribution
        cur.execute("""
            SELECT
                SUM(CASE WHEN total_score >= 70 THEN 1 ELSE 0 END) as high,
                SUM(CASE WHEN total_score >= 40 AND total_score < 70 THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN total_score >= 15 AND total_score < 40 THEN 1 ELSE 0 END) as low,
                SUM(CASE WHEN total_score < 15 THEN 1 ELSE 0 END) as rejected_range
            FROM news_analysis WHERE total_score > 0
        """)
        row = cur.fetchone()
        if row:
            if _is_postgres():
                funnel["score_distribution"] = {"high": row[0] or 0, "medium": row[1] or 0, "low": row[2] or 0, "rejected_range": row[3] or 0}
            else:
                funnel["score_distribution"] = {"high": row[0] or 0, "medium": row[1] or 0, "low": row[2] or 0, "rejected_range": row[3] or 0}

        funnel["by_source"] = by_source
        return funnel
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        cur.close()


def get_cost_by_source():
    """API cost broken down by source (via news_id correlation)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT n.source,
                   COUNT(c.id) as api_calls,
                   COALESCE(SUM(c.cost_usd), 0) as total_cost,
                   COALESCE(AVG(c.latency_ms), 0) as avg_latency
            FROM api_cost_log c
            JOIN news n ON c.news_id = n.id
            WHERE c.news_id != ''
            GROUP BY n.source
            ORDER BY total_cost DESC
        """)
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            rows = [dict(r) for r in cur.fetchall()]
        return {"by_source": rows}
    except Exception as e:
        return {"status": "error", "message": str(e), "by_source": []}
    finally:
        cur.close()


def get_prompt_insights():
    """Prompt version performance: avg cost, latency, usage count per version."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Get all prompt versions
        cur.execute("SELECT id, prompt_name, version, is_active, created_at, notes FROM prompt_versions ORDER BY prompt_name, version DESC")
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            versions = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            versions = [dict(r) for r in cur.fetchall()]

        # Get LLM API call stats
        ph = "%s" if _is_postgres() else "?"
        cur.execute("""
            SELECT model,
                   COUNT(*) as call_count,
                   COALESCE(SUM(cost_usd), 0) as total_cost,
                   COALESCE(AVG(cost_usd), 0) as avg_cost,
                   COALESCE(AVG(latency_ms), 0) as avg_latency,
                   COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                   COALESCE(SUM(tokens_out), 0) as total_tokens_out
            FROM api_cost_log
            WHERE api_type = 'llm'
            GROUP BY model
            ORDER BY call_count DESC
        """)
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            model_stats = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            model_stats = [dict(r) for r in cur.fetchall()]

        # Get daily LLM cost trend (last 14 days)
        cutoff = (dt_mod.now(timezone.utc) - timedelta(days=14)).isoformat()
        day_expr = "CAST(created_at AS TEXT)" if _is_postgres() else "created_at"
        cur.execute(f"""
            SELECT SUBSTRING({day_expr}, 1, 10) as day,
                   COUNT(*) as calls,
                   COALESCE(SUM(cost_usd), 0) as cost
            FROM api_cost_log
            WHERE api_type = 'llm' AND created_at > {ph}
            GROUP BY SUBSTRING({day_expr}, 1, 10)
            ORDER BY day
        """, (cutoff,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            daily_trend = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            daily_trend = [dict(r) for r in cur.fetchall()]

        # Prompt usage summary by name
        by_name = {}
        for v in versions:
            name = v["prompt_name"]
            if name not in by_name:
                by_name[name] = {"versions": 0, "active_version": None}
            by_name[name]["versions"] += 1
            if v.get("is_active"):
                by_name[name]["active_version"] = v["version"]

        return {
            "versions": versions,
            "model_stats": model_stats,
            "daily_trend": daily_trend,
            "by_name": by_name,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "versions": [], "model_stats": []}
    finally:
        cur.close()


def get_cost_summary():
    try:
        from core.observability import get_cost_summary as _get_cost_summary_impl
        return _get_cost_summary_impl(days=1)
    except Exception as e:
        return {"status": "error", "message": str(e), "total_cost_usd": 0, "total_calls": 0, "by_type": []}
