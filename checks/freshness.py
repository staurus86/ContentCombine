from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re


def _parse_date(date_str: str) -> datetime | None:
    """Пытается распарсить дату из разных форматов."""
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()

    # 1. ISO format (2026-03-07T15:30:00+00:00, 2026-03-07T15:30:00Z, 2026-03-07)
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass

    # 2. RFC 822 / RFC 2822 (from RSS feeds: "Wed, 07 Mar 2026 15:30:00 GMT")
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass

    # 3. Common date formats
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",      # 2026-03-07T15:30:00.000+03:00
        "%Y-%m-%dT%H:%M:%S.%f",          # 2026-03-07T15:30:00.000
        "%Y-%m-%d %H:%M:%S",             # 2026-03-07 15:30:00
        "%Y-%m-%d %H:%M",                # 2026-03-07 15:30
        "%d.%m.%Y %H:%M",                # 07.03.2026 15:30
        "%d.%m.%Y",                       # 07.03.2026
        "%d %b %Y",                       # 07 Mar 2026
        "%d %B %Y",                       # 07 March 2026
        "%b %d, %Y",                      # Mar 07, 2026
        "%B %d, %Y",                      # March 07, 2026
        "%Y/%m/%d",                       # 2026/03/07
        "%m/%d/%Y",                       # 03/07/2026
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    # 4. Extract date-like pattern from messy string (e.g. "Published: March 7, 2026 at 3:30 PM")
    iso_match = re.search(r'\d{4}-\d{2}-\d{2}', date_str)
    if iso_match:
        try:
            dt = datetime.strptime(iso_match.group(), "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    return None


def check_freshness(news: dict) -> dict:
    # Try published_at first, then parsed_at as fallback
    published_at = news.get("published_at", "")
    parsed_at = news.get("parsed_at", "")

    pub = _parse_date(published_at)
    if pub is None:
        pub = _parse_date(parsed_at)
    if pub is None:
        return {"age_hours": -1, "status": "unknown", "score": 30, "pass": False}

    now = datetime.now(timezone.utc)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)

    age_hours = (now - pub).total_seconds() / 3600

    # Guard against future dates (clock skew)
    if age_hours < 0:
        age_hours = 0

    if age_hours < 2:
        status = "hot"
        score = 100
    elif age_hours < 6:
        status = "fresh"
        score = 80
    elif age_hours < 24:
        status = "today"
        score = 50
    elif age_hours < 72:
        status = "recent"
        score = 25
    else:
        status = "old"
        score = 10

    return {
        "age_hours": round(age_hours, 1),
        "status": status,
        "score": score,
        "pass": age_hours < 72,
    }
