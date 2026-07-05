"""Фактчек апдейтов Google через официальный Search Status Dashboard.

Новость «Google выкатил core update» получает высокий viral просто по словам —
без сверки с реальностью. Google Search Status feed даёт ПОДТВЕРЖДЁННЫЕ апдейты
с именем, статусом и периодом раската. Матчим новость с этим списком и ставим
метку «подтверждено Google» — ровно то, что делает журналист перед публикацией.

Sprint 1B (quality-journalism). Fails open: сбой feed → пустой список, метки нет.
"""

import logging
import re
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_STATUS_FEED = "https://status.search.google.com/en/feed.atom"
_CACHE_TTL = 6 * 3600  # апдейты меняются редко — 6ч кэша достаточно
_LOOKBACK_DAYS = 120   # апдейт обсуждают неделями после раската

# «RESOLVED: June 2026 spam update» / «ONGOING: ...» — статус-префикс перед именем
_STATUS_PREFIX = re.compile(r"^\s*(RESOLVED|ONGOING|AVAILABLE|UPDATE)\s*:\s*", re.IGNORECASE)
_BEGAN = re.compile(r"began at\s*.*?(\d{4}-\d{2}-\d{2})", re.IGNORECASE | re.DOTALL)
_ENDED = re.compile(r"ended at\s*.*?(\d{4}-\d{2}-\d{2})", re.IGNORECASE | re.DOTALL)


def _parse_updates(feed_bytes: bytes) -> list[dict]:
    """Парсит Atom-feed Search Status в список апдейтов."""
    import feedparser
    parsed = feedparser.parse(feed_bytes)
    updates = []
    for e in parsed.entries:
        raw_title = (e.get("title") or "").strip()
        if not raw_title:
            continue
        name = _STATUS_PREFIX.sub("", raw_title).strip()
        resolved = bool(re.match(r"^\s*RESOLVED", raw_title, re.IGNORECASE))
        summary = e.get("summary", "") or ""
        began = _BEGAN.search(summary)
        ended = _ENDED.search(summary)
        updated = e.get("updated", "") or e.get("published", "") or ""
        updates.append({
            "name": name,
            "name_lower": name.lower(),
            "resolved": resolved,
            "status": "resolved" if resolved else "ongoing",
            "began": began.group(1) if began else "",
            "ended": ended.group(1) if ended else "",
            "updated": updated[:10],
            "url": e.get("link", ""),
        })
    return updates


def get_confirmed_updates() -> list[dict]:
    """Список подтверждённых Google апдейтов за последние ~120 дней. Кэшируется."""
    from apis.cache import cache_get, cache_set
    ck = "google_status_updates"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    updates = []
    try:
        import requests
        resp = requests.get(_STATUS_FEED, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0 (ContentCombine)"})
        if resp.status_code == 200:
            all_updates = _parse_updates(resp.content)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            updates = [u for u in all_updates if (u.get("updated") or u.get("began") or "") >= cutoff]
    except Exception as e:
        logger.warning("Search Status feed fetch failed: %s", e)
    cache_set(ck, updates, ttl=_CACHE_TTL)
    return updates


# Новость реально ЗАЯВЛЯЕТ апдейт (а не просто упоминает слово)
_UPDATE_SIGNAL = re.compile(
    r"core update|spam update|ranking update|helpful content|product review|"
    r"reviews update|broad core|апдейт|обновлени[ея] алгоритма|базов[ыо][йм] апдейт",
    re.IGNORECASE,
)


def verify_update(title: str, text: str = "") -> dict:
    """Сверяет новость с подтверждёнными апдейтами Google.

    Возвращает {"confirmed": bool, "name": str, "status": str, "url": str}.
    confirmed=True только если новость заявляет апдейт И его имя есть в Search Status.
    """
    blob = f"{title or ''} {text or ''}".lower()
    if not _UPDATE_SIGNAL.search(blob):
        return {"confirmed": False}
    for u in get_confirmed_updates():
        nl = u["name_lower"]
        # имя апдейта целиком есть в тексте («june 2026 spam update»), либо
        # совпал и тип, и месяц-год (устойчиво к перестановке слов)
        if nl and nl in blob:
            return {"confirmed": True, "name": u["name"], "status": u["status"], "url": u["url"]}
        parts = nl.split()
        if len(parts) >= 3:
            kind = parts[-2] + " " + parts[-1]  # «spam update» / «core update»
            month = parts[0]                     # «june» — год опускаем: в окне 120д
            if kind in blob and month in blob:   # месяц+тип уникальны, «Done» без года тоже ловим
                return {"confirmed": True, "name": u["name"], "status": u["status"], "url": u["url"]}
    return {"confirmed": False}
