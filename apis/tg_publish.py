"""One-click Telegram publishing (free tier, no LLM).

Builds a plain-text post (no parse_mode → no escaping bugs) from a news item:
title · summary (trimmed) · hashtags · url, capped at Telegram's 4096 limit.
Sends to config.TELEGRAM_PUBLISH_CHANNEL via the bot if token+channel are set;
otherwise returns {"status": "no_token", "text": ...} so the UI copies it.
"""
import json
import logging
import re

import config
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

TG_LIMIT = 4096
SUMMARY_MAX = 600


def _hashtags(tags, entities, limit=5):
    out, seen = [], set()
    pool = list(tags or []) + list(entities or [])
    for src in pool:
        label = (src.get("label") or src.get("id") or "") if isinstance(src, dict) else str(src)
        tag = re.sub(r"[^\w]+", "", label, flags=re.UNICODE)  # keep letters/digits/_ (Cyrillic ok)
        if len(tag) < 2 or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append("#" + tag)
        if len(out) >= limit:
            break
    return " ".join(out)


def build_post(news: dict, tags=None, entities=None) -> str:
    title = (news.get("title") or news.get("h1") or "").strip()
    summary = (news.get("description") or news.get("plain_text") or "").strip()
    if len(summary) > SUMMARY_MAX:
        summary = summary[:SUMMARY_MAX].rsplit(" ", 1)[0] + "…"
    url = (news.get("url") or "").strip()
    hashtags = _hashtags(tags, entities)
    parts = [p for p in (title, summary, hashtags, url) if p]
    text = "\n\n".join(parts)
    if len(text) > TG_LIMIT:
        text = text[:TG_LIMIT - 1] + "…"
    return text


def _load_news_post(news_id: str):
    conn = get_connection()
    cur = conn.cursor()
    _ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"""
            SELECT n.title, n.h1, n.description, n.plain_text, n.url,
                   COALESCE(a.tags_data, '[]'), COALESCE(a.entity_names, '[]')
            FROM news n LEFT JOIN news_analysis a ON a.news_id = n.id
            WHERE n.id = {_ph}
        """, (news_id,))
        row = cur.fetchone()
        if not row:
            return None
        news = {"title": row[0], "h1": row[1], "description": row[2],
                "plain_text": row[3], "url": row[4]}
        try:
            tags = json.loads(row[5]) if isinstance(row[5], str) else (row[5] or [])
        except Exception:
            tags = []
        try:
            ents = json.loads(row[6]) if isinstance(row[6], str) else (row[6] or [])
        except Exception:
            ents = []
        return build_post(news, tags, ents)
    finally:
        cur.close()


def publish(body: dict) -> dict:
    """body: {news_id} or {text}. Returns status ok / no_token / error, always with text."""
    text = (body.get("text") or "").strip()
    if not text and body.get("news_id"):
        text = _load_news_post(body["news_id"]) or ""
    if not text:
        return {"status": "error", "message": "text or news_id required"}
    if len(text) > TG_LIMIT:
        text = text[:TG_LIMIT - 1] + "…"

    # Preview: just return the composed text, never send.
    if body.get("preview"):
        return {"status": "preview", "text": text}

    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    channel = getattr(config, "TELEGRAM_PUBLISH_CHANNEL", "")
    if not token or not channel:
        return {"status": "no_token", "text": text}

    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": channel, "text": text, "disable_web_page_preview": False},
            timeout=20,
        )
        data = resp.json()
        if data.get("ok"):
            return {"status": "ok", "text": text}
        return {"status": "error", "message": data.get("description", "send failed"), "text": text}
    except Exception as e:
        logger.error("Telegram publish failed: %s", e)
        return {"status": "error", "message": str(e), "text": text}
