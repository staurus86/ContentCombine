"""Cross-source scoring — вес источника на основе истории одобрений."""

import logging
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

# Базовые веса под нишу SEO / AI-поиск. Имена должны совпадать с config.SOURCES.
# Не перечисленные источники получают 1.0. Обучаемая часть (решения редактора)
# применяется отдельно через checks/feedback.py — здесь только априорный авторитет.
DEFAULT_WEIGHTS = {
    # Первоисточники поисковых систем
    "Google Search Central": 1.3,
    "Google Search Status": 1.3,
    "Google Keyword Search": 1.2,
    "The Keyword (Google)": 1.1,
    "Google Search Central YouTube": 1.1,
    "Yandex Webmaster RU": 1.3,
    "TG:Яндекс Вебмастер": 1.3,
    "Bing Webmaster Blog": 1.2,
    "Bing Search Blog": 1.1,
    # Официальные блоги AI-платформ
    "OpenAI News": 1.2,
    "Anthropic News": 1.2,
    "Google AI blog.google": 1.2,
    "Google Gemini blog": 1.1,
    "Google DeepMind Blog": 1.1,
    "Mistral AI News": 1.1,
    # Отраслевые издания
    "Search Engine Land": 1.2,
    "Search Engine Roundtable": 1.2,
    "Search Engine Journal": 1.1,
    "RU: SEOnews": 1.1,
    # Признанные эксперты и аналитика
    "Marie Haynes": 1.1,
    "Lily Ray": 1.1,
    "Lily Ray Substack": 1.1,
    "GSQi": 1.1,
    "Aleyda SEO Blog": 1.1,
    "iPullRank": 1.1,
    "SparkToro": 1.1,
    "Growth Memo": 1.1,
    "Ahrefs Blog": 1.1,
    "Semrush Blog": 1.1,
    "TG:Devaka Talk": 1.1,
    # Bluesky: гуглеры и топ-эксперты
    "BS:searchliaison": 1.2,
    "BS:dannysullivan": 1.2,
    "BS:garyillyes": 1.2,
    "BS:lilyray": 1.1,
    "BS:glenngabe": 1.1,
    "BS:mariehaynes": 1.1,
    "BS:seroundtable": 1.1,
    "BS:aleyda": 1.1,
    "BS:cyrusshepard": 1.1,
    "BS:kevinindig": 1.1,
    "BS:randfish": 1.1,
    "BS:mordy": 1.1,
    # Смежные/шумные источники — слегка ниже
    "TechCrunch AI": 0.9,
    "VentureBeat AI": 0.9,
    "MarTech": 0.9,
    "Press Gazette": 0.9,
    "The Neuron": 0.9,
    "TLDR Marketing": 0.9,
    "Niche Pursuits": 0.9,
    "Neil Patel Blog": 0.9,
    "Search Engine Watch": 0.9,
    "DE: t3n SEO/Digital": 0.9,
}


def get_source_weight(source: str) -> float:
    """Возвращает априорный вес источника.

    История одобрений сюда больше не подмешивается: авто-статусы (processed/ready)
    самоусиливали вес, а человеческие решения уже учитываются отдельным механизмом
    feedback_adjustment в checks/pipeline.py — двойной учёт убран."""
    base = DEFAULT_WEIGHTS.get(source, 1.0)
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
