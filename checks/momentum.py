"""Momentum scoring — скорость распространения новости.

Оптимизировано: батч-режим — один DB-запрос на весь батч вместо N запросов.
"""

import logging
from datetime import datetime, timezone, timedelta
from checks.deduplication import normalize
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

# Batch cache: stores recent news fetched once per pipeline run
_batch_cache = {"data": None, "ts": 0}
_CACHE_TTL_SECONDS = 30  # cache valid for 30 seconds


def _word_overlap(title1: str, title2: str) -> float:
    """Быстрое сравнение заголовков по пересечению слов (без TF-IDF)."""
    words1 = set(normalize(title1).split())
    words2 = set(normalize(title2).split())
    words1 = {w for w in words1 if len(w) > 2}
    words2 = {w for w in words2 if len(w) > 2}
    if not words1 or not words2:
        return 0
    return len(words1 & words2) / min(len(words1), len(words2))


def _get_recent_news() -> list[dict]:
    """Fetches recent news from DB with batch caching (30s TTL)."""
    import time
    now_ts = time.monotonic()

    if _batch_cache["data"] is not None and (now_ts - _batch_cache["ts"]) < _CACHE_TTL_SECONDS:
        return _batch_cache["data"]

    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()

    try:
        cur.execute(f"""
            SELECT id, source, title, parsed_at FROM news
            WHERE parsed_at > {ph}
            ORDER BY parsed_at DESC
            LIMIT 100
        """, (cutoff,))

        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            recent = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            recent = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()

    _batch_cache["data"] = recent
    _batch_cache["ts"] = now_ts
    return recent


def invalidate_cache():
    """Invalidates the batch cache (call after pipeline completes)."""
    _batch_cache["data"] = None
    _batch_cache["ts"] = 0


def get_momentum(news: dict) -> dict:
    """Проверяет сколько источников написали о похожей теме за последние часы.

    Использует batch-кэшированный запрос к БД (один на весь pipeline batch).
    """
    title = news.get("title", "")
    now = datetime.now(timezone.utc)

    recent = _get_recent_news()

    if not recent:
        return {"sources_1h": 0, "sources_6h": 0, "sources_24h": 0, "level": "none", "score": 0}

    # Быстрое сравнение по пересечению слов
    sources_1h = set()
    sources_6h = set()
    sources_24h = set()

    for r in recent:
        overlap = _word_overlap(title, r["title"])
        if overlap < 0.5:
            continue

        source = r["source"]
        parsed = r.get("parsed_at", "")
        if not parsed:
            sources_24h.add(source)
            continue

        try:
            pt = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
            if pt.tzinfo is None:
                pt = pt.replace(tzinfo=timezone.utc)
            age = (now - pt).total_seconds() / 3600

            if age <= 1:
                sources_1h.add(source)
            if age <= 6:
                sources_6h.add(source)
            sources_24h.add(source)
        except Exception:
            sources_24h.add(source)

    s1 = len(sources_1h)
    s6 = len(sources_6h)
    s24 = len(sources_24h)

    if s1 >= 4:
        level = "viral"
        score = 100
    elif s6 >= 4:
        level = "growing"
        score = 70
    elif s24 >= 3:
        level = "spreading"
        score = 40
    elif s24 >= 2:
        level = "noticed"
        score = 20
    else:
        level = "none"
        score = 0

    return {
        "sources_1h": s1,
        "sources_6h": s6,
        "sources_24h": s24,
        "similar_sources": list(sources_24h),
        "level": level,
        "score": score,
    }
