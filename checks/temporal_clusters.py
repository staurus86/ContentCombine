"""Temporal clusters — цепочки событий (одна тема, несколько дней)."""

import logging
from datetime import datetime, timezone, timedelta
from checks.deduplication import tfidf_similarity
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def get_event_chain(news: dict, days: int = 7) -> dict:
    """Ищет цепочку событий по одной теме за последние N дней."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"

    title = news.get("title", "")
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()

    try:
        cur.execute(f"""
            SELECT id, source, title, published_at, status FROM news
            WHERE parsed_at > {ph}
            ORDER BY published_at ASC
            LIMIT 1000
        """, (cutoff,))

        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            all_news = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            all_news = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()

    if len(all_news) < 2:
        return {"chain": [], "chain_length": 0, "days_span": 0, "phase": "single"}

    # Compare our title with all others (index 0 = our title)
    titles = [title] + [n["title"] for n in all_news]
    try:
        pairs = tfidf_similarity(titles)
    except Exception:
        return {"chain": [], "chain_length": 0, "days_span": 0, "phase": "single"}

    # Find matches to our news (index 0)
    similar_indices = set()
    for i, j, score in pairs:
        if i == 0 and j > 0:
            similar_indices.add(j - 1)
        elif j == 0 and i > 0:
            similar_indices.add(i - 1)

    if not similar_indices:
        return {"chain": [], "chain_length": 0, "days_span": 0, "phase": "single"}

    # Build chain sorted by date
    chain = []
    for idx in sorted(similar_indices):
        if 0 <= idx < len(all_news):
            n = all_news[idx]
            chain.append({
                "id": n["id"],
                "source": n["source"],
                "title": n["title"],
                "published_at": n.get("published_at", ""),
                "status": n.get("status", ""),
            })

    chain.sort(key=lambda x: x.get("published_at", ""))

    # Calculate span
    dates = []
    for c in chain:
        pub = c.get("published_at", "")
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                dates.append(dt)
            except Exception:
                pass

    days_span = 0
    if len(dates) >= 2:
        days_span = (dates[-1] - dates[0]).days

    # Determine phase
    if len(chain) >= 5:
        phase = "trending"
    elif len(chain) >= 3:
        phase = "developing"
    elif len(chain) >= 2:
        phase = "emerging"
    else:
        phase = "single"

    return {
        "chain": chain[:20],
        "chain_length": len(chain),
        "days_span": days_span,
        "phase": phase,
        "unique_sources": len(set(c["source"] for c in chain)),
    }
