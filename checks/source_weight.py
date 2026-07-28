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


# Выученные веса: доля попадания источника в дайджест. Пересчитываются джобой
# (pipeline.orchestrator.refresh_source_weights) и живут в app_settings.
LEARNED_KEY = "LEARNED_SOURCE_WEIGHTS"
_LEARNED_CACHE = {"data": None, "ts": 0.0}
_LEARNED_TTL = 3600
# Первоисточники не опускаем ниже априорного веса, даже если редактор берёт их
# редко: сам факт «Google подтвердил» важнее частоты попадания в дайджест.
_FLOOR_PROTECTED = 1.2


def _learned_weights() -> dict:
    import time as _t
    now = _t.monotonic()
    if _LEARNED_CACHE["data"] is not None and now - _LEARNED_CACHE["ts"] < _LEARNED_TTL:
        return _LEARNED_CACHE["data"]
    data = {}
    try:
        import json
        from storage.database import get_app_setting
        raw = get_app_setting(LEARNED_KEY)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = {k: float(v) for k, v in parsed.items()}
    except Exception as e:
        logger.debug("Learned source weights unavailable: %s", e)
    _LEARNED_CACHE["data"] = data
    _LEARNED_CACHE["ts"] = now
    return data


def invalidate_learned_weights():
    _LEARNED_CACHE["data"] = None
    _LEARNED_CACHE["ts"] = 0.0


def get_source_weight(source: str) -> float:
    """Вес источника: выученный по дайджестам, с априорным как нижней границей.

    Априорные веса ставились на глаз и разошлись с фактом: Neil Patel держал 0.9
    при доле попадания в дайджест 66.7%, t3n — те же 0.9 при 3.1%, а лучший
    источник месяца semai.ai (75.9%) шёл с нейтральной единицей.

    История одобрений сюда не подмешивается: авто-статусы (processed/ready)
    самоусиливали вес. Обучаемся на решениях LLM-редактора в дайджестах."""
    base = DEFAULT_WEIGHTS.get(source, 1.0)
    learned = _learned_weights().get(source)
    if learned is None:
        return round(max(0.5, min(2.0, base)), 2)
    if base >= _FLOOR_PROTECTED:
        learned = max(learned, base)
    return round(max(0.5, min(2.0, learned)), 2)


MIN_SELECTED_FOR_LEARNING = 150  # меньше — статистика шумит, веса не трогаем


def compute_learned_weights(days: int = 30, min_items: int = 8) -> dict:
    """Считает веса по доле попадания материалов источника в ТЕКСТ дайджеста.

        weight = 0.7 + 0.9 × (picked + 3·p̄) / (total + 3)

    Учимся только на `selected = 1` — на материалах, которые редактор реально
    поставил в дайджест. Кандидаты не годятся: их отбирает сам скоринг, и вес,
    выученный на них, просто усиливал бы его собственные решения (той же ошибкой
    раньше страдал source_weight, обучавшийся на авто-статусах).

    Сглаживание тремя «средними» материалами не даёт источнику с двумя удачными
    публикациями улететь наверх. Диапазон получается 0.7–1.6.
    Источники тише min_items за период остаются на априорном весе.
    Пока размеченных материалов меньше MIN_SELECTED_FOR_LEARNING — возвращает {}."""
    from storage.database import get_connection, _is_postgres
    conn = get_connection()
    cur = conn.cursor()
    try:
        if _is_postgres():
            cur.execute(f"""SELECT COUNT(*) FROM digest_history
                WHERE COALESCE(selected, 0) = 1
                  AND digest_date >= TO_CHAR(NOW() - INTERVAL '{int(days)} days', 'YYYY-MM-DD')""")
        else:
            cur.execute(f"""SELECT COUNT(*) FROM digest_history
                WHERE COALESCE(selected, 0) = 1 AND digest_date >= date('now', '-{int(days)} days')""")
        labelled = int((cur.fetchone() or [0])[0] or 0)
        if labelled < MIN_SELECTED_FOR_LEARNING:
            logger.info("Learned source weights: размеченных материалов %d из %d — ждём данных",
                        labelled, MIN_SELECTED_FOR_LEARNING)
            return {}

        if _is_postgres():
            cur.execute(f"""
                WITH picked AS (
                    SELECT DISTINCT news_id FROM digest_history
                    WHERE COALESCE(selected, 0) = 1
                      AND digest_date >= TO_CHAR(NOW() - INTERVAL '{int(days)} days', 'YYYY-MM-DD')
                )
                SELECT n.source, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE p.news_id IS NOT NULL) AS picked
                FROM news n
                LEFT JOIN picked p ON p.news_id = n.id
                WHERE n.parsed_at::timestamptz > (NOW() - INTERVAL '{int(days)} days')
                GROUP BY n.source
            """)
        else:
            cur.execute(f"""
                SELECT n.source, COUNT(*) AS total,
                       SUM(CASE WHEN d.news_id IS NOT NULL THEN 1 ELSE 0 END) AS picked
                FROM news n
                LEFT JOIN (SELECT DISTINCT news_id FROM digest_history
                           WHERE COALESCE(selected, 0) = 1
                             AND digest_date >= date('now', '-{int(days)} days')) d
                       ON d.news_id = n.id
                WHERE n.parsed_at > datetime('now', '-{int(days)} days')
                GROUP BY n.source
            """)
        rows = [(r[0], int(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()]
    finally:
        cur.close()

    total_all = sum(t for _, t, _ in rows)
    picked_all = sum(p for _, _, p in rows)
    if not total_all or not picked_all:
        return {}
    mean_rate = picked_all / total_all

    weights = {}
    for source, total, picked in rows:
        if total < min_items:
            continue
        smoothed = (picked + 3 * mean_rate) / (total + 3)
        weights[source] = round(max(0.6, min(1.6, 0.7 + 0.9 * smoothed)), 2)
    return weights


def save_learned_weights(days: int = 30) -> dict:
    """Пересчитывает и сохраняет веса. Возвращает {"count", "sample"} для лога."""
    import json
    from storage.database import set_app_setting
    weights = compute_learned_weights(days=days)
    if not weights:
        logger.info("Learned source weights: нет данных для пересчёта")
        return {"count": 0, "sample": {}}
    set_app_setting(LEARNED_KEY, json.dumps(weights, ensure_ascii=False))
    invalidate_learned_weights()
    top = dict(sorted(weights.items(), key=lambda x: -x[1])[:5])
    logger.info("Learned source weights: обновлено %d источников, топ: %s", len(weights), top)
    return {"count": len(weights), "sample": top}


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
