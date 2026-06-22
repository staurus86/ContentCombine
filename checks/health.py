"""Health Monitor — проверка работоспособности источников."""

import logging
from datetime import datetime, timezone, timedelta
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def get_sources_health() -> list[dict]:
    """Возвращает статус здоровья каждого источника за последние 24ч."""
    import config

    conn = get_connection()
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_3h = (now - timedelta(hours=3)).isoformat()
    cutoff_dead = (now - timedelta(days=config.SOURCE_DEAD_DAYS)).isoformat()

    ph = "%s" if _is_postgres() else "?"

    try:
        # last_parsed — абсолютная дата последней публикации (по всей истории),
        # count_24h — активность за сутки. "Мёртвость" определяем по времени
        # молчания (cutoff_dead), а не по нулю за 24ч: редкие официальные
        # источники публикуют раз в неделю и не должны висеть как dead.
        cur.execute(f"""
            SELECT source,
                   SUM(CASE WHEN parsed_at > {ph} THEN 1 ELSE 0 END) as count_24h,
                   MAX(parsed_at) as last_parsed
            FROM news
            GROUP BY source
            ORDER BY count_24h DESC
        """, (cutoff_24h,))

        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()

    # Build lookup by source name
    db_sources = {row["source"]: row for row in rows}

    # Include ALL configured sources
    all_source_names = [s["name"] for s in config.SOURCES]
    # Also include sources from DB that aren't in config
    for row in rows:
        if row["source"] not in all_source_names:
            all_source_names.append(row["source"])

    results = []
    for name in all_source_names:
        row = db_sources.get(name)
        if row:
            last_parsed = row["last_parsed"] or ""
            count = row["count_24h"] or 0
        else:
            last_parsed = ""
            count = 0

        # Determine health status (мёртвость — по времени молчания, не по 0 за 24ч)
        if not last_parsed:
            status = "dead"          # никогда ничего не публиковал
        elif last_parsed <= cutoff_dead:
            status = "dead"          # молчит дольше порога SOURCE_DEAD_DAYS
        elif last_parsed > cutoff_3h:
            status = "healthy" if count >= 10 else "low"
        elif last_parsed > cutoff_24h:
            status = "warning"
        else:
            status = "down"          # жив, но публикует редко (1д…порог) — не dead

        # Calculate minutes since last parse
        minutes_ago = -1
        if last_parsed:
            try:
                lp = datetime.fromisoformat(last_parsed.replace("Z", "+00:00"))
                if lp.tzinfo is None:
                    lp = lp.replace(tzinfo=timezone.utc)
                minutes_ago = int((datetime.now(timezone.utc) - lp).total_seconds() / 60)
            except Exception:
                pass

        results.append({
            "source": name,
            "count_24h": count,
            "last_parsed": last_parsed,
            "minutes_ago": minutes_ago,
            "status": status,
        })

    # Sort: dead first, then by count desc
    results.sort(key=lambda x: (0 if x["status"] == "dead" else 1, -x["count_24h"]))

    return results
