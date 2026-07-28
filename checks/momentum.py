"""Momentum scoring — скорость распространения новости.

Оптимизировано: батч-режим — один DB-запрос на весь батч вместо N запросов.
"""

import logging
from datetime import datetime, timezone, timedelta
from checks.deduplication import normalize
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

# Batch cache: stores recent news fetched once per pipeline run + TF-IDF индекс
_batch_cache = {"data": None, "ts": 0, "vec": None, "matrix": None}
_CACHE_TTL_SECONDS = 30  # cache valid for 30 seconds

# Пороги как в кластеризации сюжетов: лексический пол 0.15 отбирает кандидатов,
# дальше пара принимается либо по сильной лексике (0.28), либо по общей сущности.
# Голый порог 0.28 давал те же 4% срабатываний, что и старое пересечение слов;
# порог 0.15 без проверки сущностей склеивал шаблонные заголовки («How to find
# competitors' keywords» и «How to find AI visibility gaps» — 5 «источников»).
_SIM_FLOOR = 0.15
_SIM_STRONG = 0.28


def _word_overlap(title1: str, title2: str) -> float:
    """Быстрое сравнение заголовков по пересечению слов (без TF-IDF)."""
    words1 = set(normalize(title1).split())
    words2 = set(normalize(title2).split())
    words1 = {w for w in words1 if len(w) > 2}
    words2 = {w for w in words2 if len(w) > 2}
    if not words1 or not words2:
        return 0
    return len(words1 & words2) / min(len(words1), len(words2))


def _similar_indices(title: str, recent: list[dict]) -> list[int]:
    """Индексы недавних новостей об этом же событии.

    Было: пересечение слов заголовка ≥ 0.5. У двух изданий об одном событии оно
    обычно 0.2–0.4, поэтому momentum давал ноль у 93% материалов и компонент не
    работал вовсе. Теперь TF-IDF-косинус (тот же механизм, что в дедупе и трендах)
    с порогом 0.28; словесное пересечение оставлено как второй шанс для коротких
    заголовков, которые TF-IDF недооценивает."""
    idx = set()
    vec, matrix = _batch_cache.get("vec"), _batch_cache.get("matrix")
    if vec is not None and matrix is not None:
        try:
            from sklearn.metrics.pairwise import cosine_similarity
            from nlp.entities import find_entities
            sims = cosine_similarity(vec.transform([normalize(title)]), matrix)[0]
            own_ents = None
            ent_cache = _batch_cache.get("ents") or []
            for i, s in enumerate(sims):
                if s >= _SIM_STRONG:
                    idx.add(i)
                elif s >= _SIM_FLOOR:
                    if own_ents is None:
                        own_ents = {e["name"] for e in find_entities(title)}
                    if own_ents and i < len(ent_cache) and (own_ents & ent_cache[i]):
                        idx.add(i)
        except Exception as e:
            logger.debug("Momentum TF-IDF compare failed: %s", e)
    if not idx:
        idx.update(i for i, r in enumerate(recent) if _word_overlap(title, r.get("title", "")) >= 0.5)
    return sorted(idx)


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
            LIMIT 1000
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
    # TF-IDF индекс строится один раз на батч: дальше каждая новость сравнивается
    # с окном одним transform, а не N попарными проходами.
    _batch_cache["vec"] = _batch_cache["matrix"] = None
    _batch_cache["ents"] = []
    if len(recent) >= 5:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from nlp.entities import find_entities
            vec = TfidfVectorizer(ngram_range=(1, 2))
            _batch_cache["matrix"] = vec.fit_transform([normalize(r.get("title", "")) for r in recent])
            _batch_cache["vec"] = vec
            _batch_cache["ents"] = [{e["name"] for e in find_entities(r.get("title", ""))} for r in recent]
        except Exception as e:
            logger.debug("Momentum TF-IDF index build failed: %s", e)
    return recent


def invalidate_cache():
    """Invalidates the batch cache (call after pipeline completes)."""
    _batch_cache["data"] = None
    _batch_cache["ts"] = 0
    _batch_cache["vec"] = None
    _batch_cache["matrix"] = None


def get_momentum(news: dict) -> dict:
    """Проверяет сколько источников написали о похожей теме за последние часы.

    Использует batch-кэшированный запрос к БД (один на весь pipeline batch).
    """
    title = news.get("title", "")
    now = datetime.now(timezone.utc)

    recent = _get_recent_news()

    if not recent:
        return {"sources_1h": 0, "sources_6h": 0, "sources_24h": 0, "level": "none", "score": 0}

    sources_1h = set()
    sources_6h = set()
    sources_24h = set()

    for i in _similar_indices(title, recent):
        r = recent[i]
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
