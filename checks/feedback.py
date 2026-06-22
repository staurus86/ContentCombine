"""Feedback loop — обучение на решениях редактора."""

import logging
import json
from datetime import datetime, timezone, timedelta
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def init_feedback_table():
    """Создаёт таблицу для хранения feedback-статистики."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_stats (
            id TEXT PRIMARY KEY,
            stat_type TEXT,
            stat_key TEXT,
            approved INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            weight_adjustment REAL DEFAULT 0.0,
            updated_at TEXT
        )
    """)
    if not _is_postgres():
        conn.commit()


def record_decision(news_id: str, decision: str):
    """Записывает решение редактора для будущего анализа.
    decision: 'approved' или 'rejected'
    """
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"

    # Get news data
    cur.execute(f"SELECT source, title FROM news WHERE id = {ph}", (news_id,))
    row = cur.fetchone()
    if not row:
        return

    if _is_postgres():
        source = row[0]
    else:
        source = row["source"]

    # Get tags for this news
    cur.execute(f"SELECT bigrams FROM news_analysis WHERE news_id = {ph}", (news_id,))
    arow = cur.fetchone()
    tags = []
    if arow:
        try:
            raw = arow[0] if _is_postgres() else arow["bigrams"]
            tags = [b[0] for b in json.loads(raw or "[]")][:5]
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()

    # Update source stats
    _upsert_stat(cur, "source", source, decision, now)

    # Update tag stats
    for tag in tags:
        _upsert_stat(cur, "tag", tag, decision, now)

    if not _is_postgres():
        conn.commit()


def _upsert_stat(cur, stat_type: str, stat_key: str, decision: str, now: str):
    """Upsert a feedback stat row."""
    ph = "%s" if _is_postgres() else "?"
    stat_id = f"{stat_type}:{stat_key}"

    cur.execute(f"SELECT id, approved, rejected FROM feedback_stats WHERE id = {ph}", (stat_id,))
    row = cur.fetchone()

    if row:
        if _is_postgres():
            approved, rejected = row[1], row[2]
        else:
            approved, rejected = row["approved"], row["rejected"]

        if decision == "approved":
            approved += 1
        else:
            rejected += 1

        total = approved + rejected
        # Weight adjustment: positive if mostly approved, negative if mostly rejected
        rate = approved / total if total > 0 else 0.5
        adj = round((rate - 0.5) * 0.4, 3)  # -0.2 to +0.2 range

        cur.execute(f"""UPDATE feedback_stats
            SET approved = {ph}, rejected = {ph}, total = {ph},
                weight_adjustment = {ph}, updated_at = {ph}
            WHERE id = {ph}""",
            (approved, rejected, total, adj, now, stat_id))
    else:
        approved = 1 if decision == "approved" else 0
        rejected = 1 if decision == "rejected" else 0
        total = 1
        adj = 0.0
        cur.execute(f"""INSERT INTO feedback_stats
            (id, stat_type, stat_key, approved, rejected, total, weight_adjustment, updated_at)
            VALUES ({','.join([ph]*8)})""",
            (stat_id, stat_type, stat_key, approved, rejected, total, adj, now))


def get_feedback_adjustments() -> dict:
    """Возвращает все корректировки весов на основе обратной связи."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT stat_type, stat_key, approved, rejected, total, weight_adjustment FROM feedback_stats WHERE total >= 5")

        results = {"sources": {}, "tags": {}}
        if _is_postgres():
            for row in cur.fetchall():
                stype, skey, approved, rejected, total, adj = row
                bucket = "sources" if stype == "source" else "tags"
                results[bucket][skey] = {
                    "approved": approved, "rejected": rejected,
                    "total": total, "adjustment": adj,
                    "approval_rate": round(approved / total * 100, 1) if total > 0 else 0,
                }
        else:
            for row in cur.fetchall():
                row = dict(row)
                bucket = "sources" if row["stat_type"] == "source" else "tags"
                results[bucket][row["stat_key"]] = {
                    "approved": row["approved"], "rejected": row["rejected"],
                    "total": row["total"], "adjustment": row["weight_adjustment"],
                    "approval_rate": round(row["approved"] / row["total"] * 100, 1) if row["total"] > 0 else 0,
                }
        return results
    except Exception as e:
        logger.debug("Feedback error: %s", e)
        return {"sources": {}, "tags": {}}


def get_feedback_summary() -> dict:
    """Сводка для дашборда аналитики."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT stat_type, stat_key, approved, rejected, total, weight_adjustment FROM feedback_stats ORDER BY total DESC")

        sources = []
        tags = []
        if _is_postgres():
            for row in cur.fetchall():
                entry = {"key": row[1], "approved": row[2], "rejected": row[3], "total": row[4], "adj": row[5]}
                if row[0] == "source":
                    sources.append(entry)
                else:
                    tags.append(entry)
        else:
            for row in cur.fetchall():
                row = dict(row)
                entry = {"key": row["stat_key"], "approved": row["approved"], "rejected": row["rejected"],
                         "total": row["total"], "adj": row["weight_adjustment"]}
                if row["stat_type"] == "source":
                    sources.append(entry)
                else:
                    tags.append(entry)

        return {"sources": sources[:20], "tags": tags[:30]}
    except Exception as e:
        logger.debug("Feedback summary error: %s", e)
        return {"sources": [], "tags": []}
