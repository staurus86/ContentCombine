import logging
import time
from datetime import datetime, timezone, timedelta

import feedparser
from bs4 import BeautifulSoup

from storage.database import insert_news, news_exists
from parsers.proxy import fetch_with_retry, _get_random_ua
import config

MAX_AGE_DAYS = config.RSS_POST_MAX_AGE_DAYS

logger = logging.getLogger(__name__)


_JUNK_TAGS = ["script", "style", "nav", "footer", "header", "aside",
              "figure", "figcaption", "form", "button", "svg", "noscript", "iframe"]
_JUNK_CLASS_KW = ["share", "social", "bookmark", "comment", "sidebar", "related", "newsletter", "promo", "ad-",
                  # consent/cookie/подписные оверлеи: t3n (c-consent-overlay/consent-error/-initial)
                  # тянул consent-баннер + targetvideo-плеер прямо в тело статьи → мусор в биграммах/quality.
                  "consent", "cookie", "cmp", "gdpr", "paywall", "subscribe", "signup"]


def _clean_element(el) -> str:
    """Очищает элемент от мусора через get_text с предварительным удалением тегов.
    Использует str(el) для клона — минимальная аллокация по сравнению с deepcopy."""
    clone = BeautifulSoup(str(el), "lxml")
    for tag in clone.find_all(_JUNK_TAGS):
        tag.decompose()
    for div in clone.find_all(["div", "section"]):
        if getattr(div, "attrs", None) is None:  # уже decompose()-нут как потомок
            continue
        cls = div.get("class", [])
        if cls and any(kw in " ".join(cls).lower() for kw in _JUNK_CLASS_KW):
            div.decompose()
    text = clone.get_text(separator=" ", strip=True)[:5000]
    del clone
    return text


def _extract_body_text(soup) -> tuple[str, int]:
    """Извлекает основной текст статьи + число картинок."""
    from parsers.html_parser import _count_content_images
    selectors = [
        "div#article-body",
        "div[itemprop='articleBody']",
        "div.article-body", "div.article__body", "div.article-content",
        "div.post-content", "div.entry-content", "div.content-body",
        "div.story-body", "div.news-body", "div.text-body",
        "div.post__body", "div.article__content", "div.prose",
        "section.article-body", "div.content-article",
        "div[class*='article-body']", "div[class*='post-content']",
        "div[class*='articleBody']", "div[class*='entry-content']",
        "article", "main",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = _clean_element(el)
            if len(text) >= 100:
                return text, _count_content_images(el)

    if soup.body:
        text = _clean_element(soup.body)
        if len(text) >= 100:
            return text, _count_content_images(soup.body)

    return "", 0


def fetch_full_text(url: str) -> tuple[str, str, str, str, int]:
    """Загружает страницу: h1, description, plain_text, published_at, image_count."""
    try:
        resp = fetch_with_retry(url)
        # Limit HTML to 500KB to prevent OOM on huge pages
        html_text = resp.text[:512_000]
        del resp  # free response body immediately
        soup = BeautifulSoup(html_text, "lxml")
        del html_text

        h1 = ""
        h1_tag = soup.find("h1")
        if h1_tag:
            h1 = h1_tag.get_text(strip=True)

        description = ""
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if not meta_desc:
            meta_desc = soup.find("meta", attrs={"property": "og:description"})
        if meta_desc:
            description = meta_desc.get("content", "")

        # Извлекаем дату публикации
        from parsers.html_parser import _extract_publish_date
        published_at = _extract_publish_date(soup)

        # Извлекаем основной текст + картинки — умный поиск по множеству селекторов
        plain_text, image_count = _extract_body_text(soup)

        del soup  # free lxml tree immediately
        return h1, description, plain_text, published_at, image_count
    except Exception as e:
        logger.warning("Failed to fetch full text from %s: %s", url, e)
        return "", "", "", "", 0


def _record_failure(name, err):
    """Сообщить трекеру здоровья о конкретном сбое источника (DNS/404/битый фид…),
    чтобы заработали «Причина» в дашборде и авто-отключение. Возврат None из парсера
    сигналит вызывающему, что сбой уже записан (не перетирать record_success)."""
    try:
        from core.source_health import source_health
        source_health.record_failure(name, str(err))
    except Exception:
        pass


def parse_rss_source(source: dict):
    """Парсит один RSS-источник. Возвращает количество новых новостей (int),
    либо None при сбое фида (сбой уже записан в source_health)."""
    name = source["name"]
    url = source["url"]
    count = 0

    try:
        # Fetch RSS via fetch_with_retry to use proxy rotation,
        # then parse the raw content with feedparser
        try:
            resp = fetch_with_retry(url)
            feed = feedparser.parse(resp.content)
        except Exception:
            # Fallback: let feedparser fetch directly (no proxy)
            logger.debug("Proxy fetch failed for RSS %s, falling back to direct feedparser", name)
            feed = feedparser.parse(url, request_headers={"User-Agent": _get_random_ua()})
        if not feed.entries:
            # No entries can mean the proxy returned a 403 block page that parses
            # as an empty (non-bozo) feed. Feeds like Sterling Sky / SEO Notebook
            # 403 browser UAs but serve a feed-fetcher UA — retry direct once.
            logger.debug("Empty feed for %s, retrying direct with feed-fetcher UA", name)
            try:
                feed = feedparser.parse(url, request_headers={"User-Agent": "Feedfetcher-Google"})
            except Exception:
                pass
        if feed.bozo and not feed.entries:
            logger.warning("Feed error for %s: %s", name, feed.bozo_exception)
            _record_failure(name, feed.bozo_exception)
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

        for entry in feed.entries:
            link = entry.get("link", "")
            if not link or news_exists(link):
                continue

            title = entry.get("title", "").strip()
            published = entry.get("published", "") or entry.get("updated", "")

            # Фильтр по дате. Раньше смотрели только published_parsed, а Atom-фиды
            # отдают дату в updated: у Mojeek так проходили все 144 записи блога
            # с 2013 года — архив попадал в ленту как свежие новости.
            pub_dt = None
            for attr in ("published_parsed", "updated_parsed", "created_parsed"):
                val = entry.get(attr)
                if val:
                    try:
                        pub_dt = datetime(*val[:6], tzinfo=timezone.utc)
                        break
                    except (TypeError, ValueError):
                        continue
            if pub_dt and pub_dt < cutoff:
                continue

            # Текст из фида. Многие издания кладут в summary/content всю статью:
            # у VentureBeat это 5 000 знаков. Раньше всё резалось до 500, и когда
            # сайт отвечал 429 на загрузку страницы, в базу попадал огрызок —
            # quality-чек считает его слабым материалом и занижает скор.
            summary = ""
            raw_summary = ""
            content_list = entry.get("content") or []
            if content_list and isinstance(content_list, list):
                raw_summary = content_list[0].get("value", "") or ""
            if not raw_summary and "summary" in entry:
                raw_summary = entry.summary or ""
            if raw_summary:
                summary = BeautifulSoup(raw_summary, "lxml").get_text(" ", strip=True)[:5000]

            # Загружаем полный текст страницы (с задержкой чтобы не перегружать)
            time.sleep(1)
            h1, description, plain_text, page_date, image_count = fetch_full_text(link)

            if not description:
                description = summary[:500]

            # Fallback: страница не отдалась (429, блокировка) — берём текст из фида
            # целиком, а не первые 500 знаков.
            if len(plain_text or "") < len(summary):
                if not plain_text:
                    logger.debug("Text recovery for %s: using RSS content (%d chars)", link, len(summary))
                plain_text = summary

            # Приоритет: дата из RSS, иначе из HTML страницы
            final_date = published or page_date

            # Страховка для фидов без дат: возраст мог выясниться только со
            # страницы статьи. Meta Research так приносил материалы 2023 года.
            if not pub_dt and final_date:
                from parsers.html_parser import _is_too_old
                if _is_too_old(final_date):
                    logger.debug("Skip old article %s (%s)", link, final_date)
                    continue

            news_id = insert_news(
                source=name,
                url=link,
                title=title,
                h1=h1,
                description=description,
                plain_text=plain_text,
                published_at=final_date,
                image_count=image_count,
            )
            if news_id:
                count += 1

    except Exception as e:
        logger.error("Error parsing RSS %s: %s", name, e)
        _record_failure(name, e)
        return None

    logger.info("Parsed %s: %d new articles", name, count)
    return count
