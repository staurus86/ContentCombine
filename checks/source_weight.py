"""Cross-source scoring — вес источника на основе истории одобрений."""

import logging
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

# Базовые веса (по умолчанию, до обучения на данных)
DEFAULT_WEIGHTS = {
    "IGN": 1.3,
    "GameSpot": 1.2,
    "PCGamer": 1.2,
    "Eurogamer": 1.1,
    "GamesRadar": 1.1,
    "Polygon": 1.1,
    "RockPaperShotgun": 1.0,
    "GameRant": 1.0,
    "Kotaku": 1.1,
    "Destructoid": 1.0,
    "StopGame": 1.1,
    "Cybersport": 0.9,
    "Playground": 1.0,
    "Metacritic": 1.2,
    "DTF": 1.1,
    "iXBT.games": 1.0,
    "VGTimes": 0.9,
}


def get_source_weight(source: str) -> float:
    """Возвращает вес источника: базовый + корректировка по истории."""
    base = DEFAULT_WEIGHTS.get(source, 1.0)

    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"

            if _is_postgres():
                cur.execute("""
                    SELECT status, COUNT(*) as cnt FROM news
                    WHERE source = %s
                    AND parsed_at::timestamptz > (NOW() - INTERVAL '30 days')
                    GROUP BY status
                """, (source,))
                rows = cur.fetchall()
                stats = {row[0]: row[1] for row in rows}
            else:
                cur.execute(f"""
                    SELECT status, COUNT(*) as cnt FROM news
                    WHERE source = {ph}
                    AND parsed_at > datetime('now', '-30 days')
                    GROUP BY status
                """, (source,))
                stats = {row["status"]: row["cnt"] for row in cur.fetchall()}
        finally:
            cur.close()

        approved = stats.get("approved", 0) + stats.get("processed", 0) + stats.get("ready", 0)
        rejected = stats.get("rejected", 0)
        total = approved + rejected

        if total >= 10:
            approval_rate = approved / total
            if approval_rate >= 0.8:
                base += 0.2
            elif approval_rate >= 0.6:
                base += 0.1
            elif approval_rate < 0.3:
                base -= 0.2
            elif approval_rate < 0.5:
                base -= 0.1
    except Exception as e:
        logger.debug("Source weight DB error: %s", e)

    return round(max(0.5, min(2.0, base)), 2)


def get_all_source_weights() -> dict:
    """Возвращает веса всех известных источников."""
    weights = {}
    for source in DEFAULT_WEIGHTS:
        weights[source] = get_source_weight(source)
    return weights


def get_source_stats() -> list[dict]:
    """Возвращает статистику по источникам за 30 дней."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            if _is_postgres():
                cur.execute("""
                    SELECT source, status, COUNT(*) as cnt FROM news
                    WHERE parsed_at::timestamptz > (NOW() - INTERVAL '30 days')
                    GROUP BY source, status
                    ORDER BY source
                """)
                columns = [desc[0] for desc in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT source, status, COUNT(*) as cnt FROM news
                    WHERE parsed_at > datetime('now', '-30 days')
                    GROUP BY source, status
                    ORDER BY source
                """)
                rows = [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()

        # Aggregate by source
        sources = {}
        for row in rows:
            src = row["source"]
            if src not in sources:
                sources[src] = {"source": src, "total": 0, "approved": 0, "rejected": 0, "new": 0}
            sources[src]["total"] += row["cnt"]
            if row["status"] in ("approved", "processed", "ready"):
                sources[src]["approved"] += row["cnt"]
            elif row["status"] in ("rejected",):
                sources[src]["rejected"] += row["cnt"]
            elif row["status"] == "new":
                sources[src]["new"] += row["cnt"]

        result = []
        for src, data in sources.items():
            total_decisions = data["approved"] + data["rejected"]
            data["approval_rate"] = round(data["approved"] / total_decisions * 100, 1) if total_decisions > 0 else 0
            data["weight"] = get_source_weight(src)
            result.append(data)

        result.sort(key=lambda x: x["total"], reverse=True)
        return result
    except Exception as e:
        logger.warning("Source stats error: %s", e)
        return []
