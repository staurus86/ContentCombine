"""Bluesky (AT Protocol) parser — free API, no auth required."""

import logging
import time
from datetime import datetime, timezone, timedelta

import requests

from storage.database import insert_news, news_exists

logger = logging.getLogger(__name__)

BLUESKY_API = "https://public.api.bsky.app/xrpc"
MAX_AGE_DAYS = 7


def _truncate_title(text: str, max_len: int = 100) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 20:
        return truncated[:last_space]
    return truncated


def _extract_url_from_post(post: dict) -> str:
    """Extract external URL from post embed, or build Bluesky post URL."""
    record = post.get("record", {})
    embed = post.get("embed", {})

    # Check for external link embed
    if embed.get("$type") == "app.bsky.embed.external#view":
        ext = embed.get("external", {})
        uri = ext.get("uri", "")
        if uri:
            return uri

    # Check for record embed with external
    if embed.get("$type") == "app.bsky.embed.recordWithMedia#view":
        media = embed.get("media", {})
        if media.get("$type") == "app.bsky.embed.external#view":
            ext = media.get("external", {})
            uri = ext.get("uri", "")
            if uri:
                return uri

    # Fallback: construct Bluesky post URL
    author = post.get("author", {})
    handle = author.get("handle", "")
    uri = post.get("uri", "")
    # URI format: at://did:plc:xxx/app.bsky.feed.post/rkey
    rkey = uri.split("/")[-1] if uri else ""
    if handle and rkey:
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    return uri


def parse_bluesky_source(source: dict) -> int:
    """Parse a Bluesky account feed, returns count of new articles."""
    name = source.get("name", "Bluesky")
    handle = source.get("handle", "")
    if not handle:
        logger.warning("No handle configured for Bluesky source %s", name)
        return 0

    count = 0

    try:
        resp = requests.get(
            f"{BLUESKY_API}/app.bsky.feed.getAuthorFeed",
            params={"actor": handle, "limit": 20, "filter": "posts_no_replies"},
            timeout=15,
            headers={"User-Agent": "IgroNews/1.0"},
        )

        if resp.status_code != 200:
            logger.warning("Bluesky API returned %d for %s", resp.status_code, handle)
            return 0

        data = resp.json()
        feed = data.get("feed", [])

        for item in feed:
            post = item.get("post", {})
            record = post.get("record", {})
            text = record.get("text", "").strip()

            # If no text, try to get alt-text from image embeds
            if not text:
                embed = post.get("embed", {})
                if embed.get("$type") == "app.bsky.embed.images#view":
                    images = embed.get("images", [])
                    alt_texts = [img.get("alt", "") for img in images if img.get("alt")]
                    if alt_texts:
                        text = " | ".join(alt_texts)
            if not text or len(text) < 10:
                continue

            url = _extract_url_from_post(post)
            if not url or news_exists(url):
                continue

            title = _truncate_title(text)
            published_at = record.get("createdAt", "")

            # Skip old posts
            if published_at:
                try:
                    pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    if pub_dt < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS):
                        continue
                except (ValueError, TypeError):
                    pass

            # Extract description from external embed if available
            description = text[:300]
            embed = post.get("embed", {})
            if embed.get("$type") == "app.bsky.embed.external#view":
                ext = embed.get("external", {})
                ext_title = ext.get("title", "")
                ext_desc = ext.get("description", "")
                if ext_title:
                    title = _truncate_title(ext_title)
                if ext_desc:
                    description = ext_desc[:300]

            news_id = insert_news(
                source=name,
                url=url,
                title=title,
                h1="",
                description=description,
                plain_text=text,
                published_at=published_at,
            )
            if news_id:
                count += 1

    except Exception as e:
        logger.error("Error parsing Bluesky %s (%s): %s", name, handle, e)
        return 0

    logger.info("Parsed Bluesky %s: %d new articles", name, count)
    return count
