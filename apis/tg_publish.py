"""One-click Telegram publishing (free tier, no LLM).

Builds a plain-text post (no parse_mode → no escaping bugs) from a news item:
title · summary (trimmed) · hashtags · url, capped at Telegram's 4096 limit.
Sends to config.TELEGRAM_PUBLISH_CHANNEL via the bot if token+channel are set;
otherwise returns {"status": "no_token", "text": ...} so the UI copies it.
"""
import html as _html
import json
import logging
import re

import config
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

TG_LIMIT = 4096
CHUNK_LIMIT = 3800  # margin under TG_LIMIT for safe splitting
SUMMARY_MAX = 600


def _md_to_html(s: str) -> str:
    """Convert digest markdown (**bold**, [text](url)) to Telegram HTML."""
    s = _html.escape(s, quote=False)  # & < >
    s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
               lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return s


def _visible_len(s: str) -> int:
    """Длина, которую считает Telegram: URL из [текст](url) уходят в entities и в
    лимит 4096 не входят. Считать сырой markdown — значит рвать на два сообщения
    дайджест, который в канал влезает одним (у дайджеста ссылок больше, чем текста)."""
    s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    return len(s.encode("utf-16-le")) // 2  # TG считает в UTF-16: эмодзи = 2


def _split_chunks(text: str, limit: int = CHUNK_LIMIT, measure=len) -> list[str]:
    """Split text into Telegram-sized chunks at paragraph boundaries."""
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        while len(block) > limit:  # a single oversized block — hard split
            if cur.strip():
                chunks.append(cur.rstrip()); cur = ""
            chunks.append(block[:limit]); block = block[limit:]
        if measure(cur) + measure(block) + 2 > limit and cur.strip():
            chunks.append(cur.rstrip()); cur = ""
        cur += block + "\n\n"
    if cur.strip():
        chunks.append(cur.rstrip())
    return chunks or [text[:limit]]


def _send_chunks(token: str, channel: str, chunks: list[str], parse_mode=None) -> dict:
    """Send each chunk as a separate message. Returns ok only if all succeed."""
    import requests
    sent = 0
    for chunk in chunks:
        payload = {"chat_id": channel, "text": chunk, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=20)
        data = resp.json()
        if not data.get("ok"):
            return {"status": "error", "message": data.get("description", "send failed"), "sent": sent}
        sent += 1
    return {"status": "ok", "sent": sent}


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
    """body: {news_id} | {text} [, markdown: bool]. Returns ok / no_token / error.

    markdown=True (digests): **bold**/[text](url) → HTML, sent with parse_mode=HTML.
    Long posts are split into multiple messages (Telegram's 4096 limit).
    """
    text = (body.get("text") or "").strip()
    if not text and body.get("news_id"):
        text = _load_news_post(body["news_id"]) or ""
    if not text:
        return {"status": "error", "message": "text or news_id required"}
    is_md = bool(body.get("markdown"))

    # Preview: return the composed text untouched, never send.
    if body.get("preview"):
        return {"status": "preview", "text": text}

    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    channel = getattr(config, "TELEGRAM_PUBLISH_CHANNEL", "")
    if not token or not channel:
        return {"status": "no_token", "text": text}

    chunks = _split_chunks(text, measure=_visible_len if is_md else len)
    parse_mode = None
    if is_md:
        chunks = [_md_to_html(c) for c in chunks]
        parse_mode = "HTML"

    try:
        result = _send_chunks(token, channel, chunks, parse_mode=parse_mode)
        result["text"] = text
        result["parts"] = len(chunks)
        return result
    except Exception as e:
        logger.error("Telegram publish failed: %s", e)
        return {"status": "error", "message": str(e), "text": text}
