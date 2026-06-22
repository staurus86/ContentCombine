"""VK wall parser — uses VK API wall.get with service token."""

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests

import config
from storage.database import insert_news, news_exists

logger = logging.getLogger(__name__)
MAX_AGE_DAYS = config.VK_POST_MAX_AGE_DAYS

# Cache resolved screen_name -> group_id (persists for process lifetime)
_screen_name_cache: dict[str, str] = {}

_HASHTAG_RE = re.compile(r"#\S+")
_LINK_RE = re.compile(r"https?://\S+")
_BRACKET_RE = re.compile(r"\[(?:id|club)\d+\|([^\]]+)\]")


def _clean_text(text: str) -> str:
    """Remove hashtags, VK mentions [id123|Name], and clean whitespace."""
    # Replace VK mentions [club123|Name] with just Name
    text = _BRACKET_RE.sub(r"\1", text)
    # Remove hashtags
    text = _HASHTAG_RE.sub("", text)
    # Remove standalone URLs (keep text around them)
    text = _LINK_RE.sub("", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_meaningful_text(post: dict) -> tuple[str, str]:
    """Extract title and description from post, trying text + attachments.
    Returns (title, plain_text). Both empty if post has no useful content."""
    raw_text = post.get("text", "").strip()
    clean = _clean_text(raw_text)

    # Try to get text from link attachment (often has title + description)
    link_title = ""
    link_desc = ""
    for att in post.get("attachments", []):
        if att.get("type") == "link":
            link = att.get("link", {})
            link_title = link.get("title", "").strip()
            link_desc = link.get("description", "").strip()
            break
        if att.get("type") == "video":
            video = att.get("video", {})
            vt = video.get("title", "").strip()
            if vt:
                link_title = vt
                link_desc = video.get("description", "").strip()
            break

    # Decision: what to use as title
    if len(clean) >= 20:
        # Post text is substantial enough
        title = clean
        plain_text = raw_text
    elif link_title:
        # Use link/video title as primary
        title = link_title
        plain_text = f"{link_title}. {link_desc}" if link_desc else link_title
        if clean:
            plain_text = f"{clean}\n\n{plain_text}"
    elif clean:
        # Short cleaned text but something
        title = clean
        plain_text = raw_text
    else:
        # Nothing useful (only hashtags/links/empty)
        return "", ""

    return title, plain_text


def _truncate_title(text: str, max_len: int = 100) -> str:
    """Truncate to max_len at word boundary."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 20:
        truncated = truncated[:last_space]
    return truncated


def _is_ad_post(post: dict) -> bool:
    if post.get("marked_as_ads", 0) == 1:
        return True
    text = post.get("text", "").lower()
    return "#ad" in text or "#реклама" in text


def _extract_url(post: dict) -> str:
    """Extract external URL from attachments, or build VK wall link."""
    for att in post.get("attachments", []):
        if att.get("type") == "link":
            url = att.get("link", {}).get("url", "")
            if url:
                return url
    owner_id = post.get("owner_id", 0)
    post_id = post.get("id", 0)
    return f"https://vk.com/wall{owner_id}_{post_id}"


def _resolve_screen_name(screen_name: str) -> str:
    """Resolve VK screen_name to numeric group_id via API. Cached."""
    if screen_name in _screen_name_cache:
        return _screen_name_cache[screen_name]
    try:
        resp = requests.get("https://api.vk.com/method/utils.resolveScreenName", params={
            "screen_name": screen_name,
            "v": config.VK_API_VERSION,
            "access_token": config.VK_API_TOKEN,
        }, timeout=10)
        data = resp.json().get("response", {})
        if data.get("type") == "group":
            gid = str(data["object_id"])
            _screen_name_cache[screen_name] = gid
            logger.info("Resolved VK screen_name '%s' -> group_id %s", screen_name, gid)
            return gid
        logger.warning("VK screen_name '%s' is not a group: %s", screen_name, data)
    except Exception as e:
        logger.error("Failed to resolve VK screen_name '%s': %s", screen_name, e)
    return ""


def parse_vk_source(source: dict) -> int:
    """Parse VK group wall, returns count of new articles."""
    if not config.VK_API_TOKEN:
        logger.warning("VK_API_TOKEN not configured, skipping VK source %s", source.get("name", ""))
        return 0

    name = source.get("name", "VK")
    group_id = source.get("group_id", "")
    # Support screen_name -> auto-resolve to group_id
    if not group_id and source.get("screen_name"):
        group_id = _resolve_screen_name(source["screen_name"])
        if not group_id:
            return 0
    count = 0

    try:
        resp = requests.get("https://api.vk.com/method/wall.get", params={
            "owner_id": f"-{group_id}",
            "count": config.VK_POSTS_BATCH_SIZE,
            "filter": "owner",
            "v": config.VK_API_VERSION,
            "access_token": config.VK_API_TOKEN,
        }, timeout=30)
        data = resp.json()

        if "error" in data:
            error_code = data["error"].get("error_code", 0)
            error_msg = data["error"].get("error_msg", "Unknown VK API error")
            if error_code == 15:
                logger.debug("VK group %s is closed/private, skipping", name)
            else:
                logger.warning("VK API error for %s: %s (code %d)", name, error_msg, error_code)
            return 0

        items = data.get("response", {}).get("items", [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

        for post in items:
            # Skip old posts
            ts = post.get("date", 0)
            if ts:
                post_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                if post_dt < cutoff:
                    continue

            if _is_ad_post(post):
                continue

            title, plain_text = _extract_meaningful_text(post)
            if not title:
                continue

            url = _extract_url(post)
            if news_exists(url):
                continue

            # Skip posts with less than 100 chars of meaningful text
            if len(plain_text) < 100:
                continue

            title = _truncate_title(title)
            ts = post.get("date", 0)
            published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""

            news_id = insert_news(
                source=name,
                url=url,
                title=title,
                h1="",
                description=plain_text[:300],
                plain_text=plain_text,
                published_at=published_at,
            )
            if news_id:
                count += 1

        time.sleep(0.4)

    except Exception as e:
        logger.error("Error parsing VK source %s: %s", name, e)
        return 0

    logger.info("Parsed VK %s: %d new articles", name, count)
    return count
