import logging
from datetime import datetime, timezone, timedelta

import config
from storage.database import insert_news, news_exists

logger = logging.getLogger(__name__)
MAX_AGE_DAYS = config.TELEGRAM_POST_MAX_AGE_DAYS

# Try to import telethon; if unavailable, fall back to RSS
try:
    from telethon.sync import TelegramClient
    from telethon.tl.types import MessageEntityUrl, MessageEntityTextUrl
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    logger.warning("telethon not installed — Telegram parser will use RSSHub fallback")


def _truncate_to_word(text: str, max_len: int = 100) -> str:
    """Обрезает текст до max_len символов по границе слова."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 20:
        return truncated[:last_space]
    return truncated


def _extract_url_from_message(message, channel_clean: str) -> str:
    """Извлекает первый URL из entities сообщения или генерирует ссылку на пост."""
    if message.entities:
        for entity in message.entities:
            if isinstance(entity, MessageEntityTextUrl):
                return entity.url
            if isinstance(entity, MessageEntityUrl):
                # URL содержится в тексте сообщения
                offset = entity.offset
                length = entity.length
                return message.text[offset:offset + length]

    return f"https://t.me/{channel_clean}/{message.id}"


def _parse_via_telethon(source: dict) -> int:
    """Парсит Telegram-канал через Telethon API."""
    from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.warning("TELEGRAM_API_ID / TELEGRAM_API_HASH not configured")
        return 0

    name = source["name"]
    channel = source.get("channel", "")
    channel_clean = channel.lstrip("@")
    include_forwards = source.get("include_forwards", False)
    count = 0

    try:
        client = TelegramClient(TELEGRAM_SESSION, int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        client.start()

        try:
            messages = client.get_messages(channel_clean, limit=config.TELEGRAM_MESSAGES_BATCH_SIZE)

            for message in messages:
                # Пропуск пустых / сервисных сообщений
                if not message.text:
                    continue

                # Пропуск пересланных, если не разрешено
                if message.forward is not None and not include_forwards:
                    continue

                url = _extract_url_from_message(message, channel_clean)

                if news_exists(url):
                    continue

                title = _truncate_to_word(message.text)
                plain_text = message.text
                published_at = message.date.astimezone(timezone.utc).isoformat()

                news_id = insert_news(
                    source=name,
                    url=url,
                    title=title,
                    h1="",
                    description="",
                    plain_text=plain_text,
                    published_at=published_at,
                )
                if news_id:
                    count += 1

        finally:
            client.disconnect()

    except Exception as e:
        logger.error("Error parsing Telegram channel %s via Telethon: %s", name, e)

    logger.info("Parsed Telegram %s (Telethon): %d new articles", name, count)
    return count


def _parse_via_rsshub(source: dict) -> int:
    """Фоллбэк: парсит Telegram-канал через RSSHub."""
    import feedparser

    name = source["name"]
    channel = source.get("channel", "")
    channel_clean = channel.lstrip("@")
    include_forwards = source.get("include_forwards", False)
    count = 0

    rss_url = f"https://rsshub.app/telegram/channel/{channel_clean}"

    try:
        feed = feedparser.parse(rss_url)
        if feed.bozo and not feed.entries:
            logger.warning("RSSHub feed error for %s: %s", name, feed.bozo_exception)
            return 0

        for entry in feed.entries:
            link = entry.get("link", "")
            if not link or news_exists(link):
                continue

            # Извлекаем текст из summary
            raw_text = ""
            if "summary" in entry:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(entry.summary, "lxml")
                raw_text = soup.get_text(strip=True)[:5000]

            if not raw_text:
                continue

            title = _truncate_to_word(entry.get("title", raw_text))
            published = entry.get("published", "")
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                published = pub_dt.isoformat()

            news_id = insert_news(
                source=name,
                url=link,
                title=title,
                h1="",
                description="",
                plain_text=raw_text,
                published_at=published,
            )
            if news_id:
                count += 1

    except Exception as e:
        logger.error("Error parsing Telegram channel %s via RSSHub: %s", name, e)

    logger.info("Parsed Telegram %s (RSSHub fallback): %d new articles", name, count)
    return count


def _parse_via_web_preview(source: dict) -> int:
    """Парсит Telegram-канал через t.me/s/ web preview (бесплатно, без API)."""
    import requests
    from bs4 import BeautifulSoup

    name = source["name"]
    channel = source.get("channel", "")
    channel_clean = channel.lstrip("@")
    count = 0

    try:
        url = f"https://t.me/s/{channel_clean}"
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        if resp.status_code != 200:
            logger.warning("Telegram web preview returned %d for %s", resp.status_code, name)
            return 0

        soup = BeautifulSoup(resp.text, "lxml")
        messages = soup.select(".tgme_widget_message")

        for msg_el in messages:
            # Извлекаем текст
            text_el = msg_el.select_one(".tgme_widget_message_text")
            if not text_el:
                continue
            text = text_el.get_text(strip=True)[:5000]
            if not text or len(text) < 20:
                continue

            # Извлекаем ссылку на сообщение
            msg_link = msg_el.get("data-post", "")
            if msg_link:
                post_url = f"https://t.me/{msg_link}"
            else:
                continue

            if news_exists(post_url):
                continue

            # Извлекаем дату
            date_el = msg_el.select_one(".tgme_widget_message_date time")
            published_at = ""
            if date_el and date_el.get("datetime"):
                published_at = date_el["datetime"]
                # Skip old posts
                try:
                    pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    if pub_dt < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS):
                        continue
                except (ValueError, TypeError):
                    pass

            # Извлекаем внешнюю ссылку (если есть)
            ext_link_el = msg_el.select_one(".tgme_widget_message_link_preview")
            ext_url = post_url
            if ext_link_el:
                href = ext_link_el.get("href", "")
                if href and "t.me" not in href:
                    ext_url = href

            title = _truncate_to_word(text)

            news_id = insert_news(
                source=name,
                url=ext_url if ext_url != post_url else post_url,
                title=title,
                h1="",
                description=text[:300],
                plain_text=text,
                published_at=published_at,
            )
            if news_id:
                count += 1

    except Exception as e:
        logger.error("Error parsing Telegram channel %s via web preview: %s", name, e)
        return -1  # signal error so caller can try RSSHub fallback

    logger.info("Parsed Telegram %s (web preview): %d new articles", name, count)
    return count


def parse_telegram_source(source: dict) -> int:
    """Парсит Telegram-канал, возвращает количество новых новостей.

    Приоритет: Telethon API → t.me/s/ web preview → RSSHub fallback.
    Web preview вызывается первым; RSSHub только если web preview упал с ошибкой
    (а не просто вернул 0 новых — это нормальная ситуация).
    """
    if TELETHON_AVAILABLE:
        from config import TELEGRAM_API_ID, TELEGRAM_API_HASH
        if TELEGRAM_API_ID and TELEGRAM_API_HASH:
            return _parse_via_telethon(source)

    # Web preview — самый надёжный бесплатный вариант
    # Возвращает >= 0 при успехе, -1 при ошибке (web preview не загрузился)
    result = _parse_via_web_preview(source)
    if result >= 0:
        return result

    # RSSHub только если web preview сломался (не просто 0 new)
    return _parse_via_rsshub(source)
