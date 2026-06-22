"""Sheets export routing helpers."""

import json

from storage.database import _is_postgres


def _safe_json_loads(val, default):
    if val is None or val == "":
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (ValueError, TypeError):
        return default


def build_check_result_from_analysis(analysis: dict | None) -> dict:
    """Convert stored analysis into check_results shape expected by NotReady export."""
    analysis = analysis or {}
    viral_triggers = _safe_json_loads(analysis.get("viral_data"), [])
    tags = _safe_json_loads(analysis.get("tags_data") or analysis.get("tags"), [])
    game_entities = _safe_json_loads(analysis.get("entity_names") or analysis.get("entities"), [])

    return {
        "checks": {
            "quality": {"score": analysis.get("quality_score", 0), "pass": True},
            "relevance": {"score": analysis.get("relevance_score", 0), "pass": True},
            "freshness": {
                "score": analysis.get("freshness_score", 0) or 0,
                "pass": True,
                "age_hours": analysis.get("freshness_hours", -1),
                "status": analysis.get("freshness_status", ""),
            },
            "viral": {
                "score": analysis.get("viral_score", 0),
                "pass": True,
                "level": analysis.get("viral_level", ""),
                "triggers": viral_triggers if isinstance(viral_triggers, list) else [],
            },
        },
        "tags": tags,
        "sentiment": {"label": analysis.get("sentiment_label", "neutral") or "neutral", "score": 0},
        "momentum": {"score": analysis.get("momentum_score", 0) or 0, "level": "none"},
        "headline": {"score": analysis.get("headline_score", 0) or 0},
        "game_entities": game_entities,
        "total_score": analysis.get("total_score", 0) or 0,
    }


def fetch_latest_rewrite(cur, news_id: str) -> dict | None:
    """Fetch most recent non-deleted article rewrite for a news item."""
    ph = "%s" if _is_postgres() else "?"
    cur.execute(
        f"""
        SELECT id, news_id, title, text, seo_title, seo_description, tags,
               style, language, original_title, original_text, source_url,
               status, created_at, updated_at
        FROM articles
        WHERE news_id = {ph} AND COALESCE(is_deleted, 0) = 0
        ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
        LIMIT 1
        """,
        (news_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    if _is_postgres():
        columns = [desc[0] for desc in cur.description]
        article = dict(zip(columns, row))
    else:
        article = dict(row)

    tags_raw = article.get("tags", "[]")
    article["tags"] = _safe_json_loads(tags_raw, [])
    return article


def export_news_by_policy(cur, news: dict, analysis: dict | None) -> dict:
    """Route a news item to the correct Sheets tab by current business policy."""
    from storage.sheets import write_not_ready_row, write_ready_row

    analysis = analysis or {}
    rewrite = fetch_latest_rewrite(cur, news.get("id", ""))
    if rewrite:
        row = write_ready_row(news, analysis, rewrite)
        return {"destination": "Ready", "row": row}

    score = analysis.get("total_score", 0) or 0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0

    if score > 60:
        row = write_not_ready_row(news, build_check_result_from_analysis(analysis))
        return {"destination": "NotReady", "row": row}

    return {
        "destination": "skip",
        "row": None,
        "reason": "No rewrite and total_score <= 60",
    }
