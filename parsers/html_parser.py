import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from urllib.parse import urljoin

from storage.database import insert_news, news_exists
from parsers.proxy import fetch_with_retry

logger = logging.getLogger(__name__)


def _is_too_old(date_str: str) -> bool:
    """True, если дата старше config.NEWS_MAX_AGE_DAYS. Пустая/непарсибельная → False
    (не блокируем — возраст неизвестен; фоновая джоба разберётся по parsed_at)."""
    if not date_str:
        return False
    from storage.database import _parse_date_loose
    d = _parse_date_loose(str(date_str))
    if d is None:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    max_days = getattr(__import__("config"), "NEWS_MAX_AGE_DAYS", 14)
    return d < datetime.now(timezone.utc) - timedelta(days=max_days)


def _date_from_url(url: str) -> str:
    """Дата из URL: /2026/06/15/ или /2026-06-15-; фолбэк год/месяц /2026/06/ → 1-е."""
    u = url or ""
    m = re.search(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", u)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), tzinfo=timezone.utc).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"/(20\d{2})/(\d{1,2})/", u)  # только год/месяц
    if m:
        y, mo = m.groups()
        try:
            return datetime(int(y), int(mo), 1, tzinfo=timezone.utc).date().isoformat()
        except ValueError:
            pass
    return ""


_JUNK_URL_RE = re.compile(
    r"/(legal|terms|privacy|cookie|cookies|authors?|category|categories|tag|tags|seo-tools|tools)(/|$)"
    r"|[^/]*-hub/?$",
    re.IGNORECASE,
)


def _is_junk_url(url: str) -> bool:
    """True для не-статей: legal/terms/privacy, author/category/tag листинги,
    product/tool страницы, *-hub, и корень домена. Консервативно (не блокирует /blog/)."""
    from urllib.parse import urlparse
    try:
        path = urlparse(url or "").path or ""
    except Exception:
        return False
    if not path.rstrip("/"):
        return True  # корень домена — не статья
    return bool(_JUNK_URL_RE.search(path))


def _record_failure(name, err):
    """Сообщить трекеру здоровья о конкретном сбое источника (см. rss_parser).
    Возврат None из парсера сигналит вызывающему, что сбой уже записан."""
    try:
        from core.source_health import source_health
        source_health.record_failure(name, str(err))
    except Exception:
        pass


def parse_html_source(source: dict):
    """Парсит HTML-страницу. Возвращает количество новых (int) либо None при сбое."""
    if source.get("type") == "homepage":
        return _parse_homepage(source)

    name = source["name"]
    url = source["url"]
    selector = source.get("selector", "article")
    title_selector = source.get("title_selector", "")
    url_pattern = source.get("url_pattern", "")
    count = 0

    try:
        resp = fetch_with_retry(url)
        soup = BeautifulSoup(resp.text, "lxml")

        items = soup.select(selector)
        if not items:
            logger.warning("No items found for %s with selector '%s'", name, selector)
            return 0

        seen_urls = set()
        for item in items[:40]:
            # Ищем ссылку внутри элемента
            a_tag = item.find("a", href=True) if item.name != "a" else item
            if not a_tag or not a_tag.get("href"):
                continue

            href = a_tag["href"]
            # Пропускаем якорные ссылки (#comments и т.п.)
            if "#" in href:
                href = href.split("#")[0]
            link = urljoin(url, href)

            # Фильтр по URL паттерну
            if url_pattern and not re.search(url_pattern, link):
                continue

            # Заголовок: кастомный селектор, или h2/h3/h4, или текст ссылки
            title = ""
            if title_selector:
                title_el = item.select_one(title_selector) if item.name != "a" else item.find(title_selector.split()[-1])
                if title_el:
                    title = title_el.get_text(strip=True)
            if not title:
                title_tag = item.find(["h2", "h3", "h4"]) or a_tag
                title = title_tag.get_text(strip=True)

            if not title or len(title) < 20:
                continue

            # Фильтр мусорных заголовков
            junk = ["комментар", "оставить", "читать далее", "подробнее", "показать"]
            if any(j in title.lower() for j in junk):
                continue

            # Дедуп URL внутри одной страницы
            if link in seen_urls:
                continue
            seen_urls.add(link)

            if news_exists(link) or _is_junk_url(link):
                continue

            time.sleep(1)
            h1, description, plain_text, published_at, image_count = _fetch_article(link)

            if _is_too_old(published_at):  # свежесть: не тащим старьё в базу
                continue

            news_id = insert_news(
                source=name,
                url=link,
                title=title,
                h1=h1,
                description=description,
                plain_text=plain_text,
                published_at=published_at,
                image_count=image_count,
            )
            if news_id:
                count += 1

    except Exception as e:
        logger.error("Error parsing HTML %s: %s", name, e)
        _record_failure(name, e)
        return None

    logger.info("Parsed %s (HTML): %d new articles", name, count)
    return count


def _parse_homepage(source: dict) -> int:
    """Универсальный парсер главной страницы: собирает ссылки из h2/h3/a.block."""
    name = source["name"]
    url = source["url"]
    domain = re.search(r'https?://([^/]+)', url).group(1) if re.search(r'https?://([^/]+)', url) else ""
    count = 0
    hard_err = None

    try:
        resp = fetch_with_retry(url)
        soup = BeautifulSoup(resp.text, "lxml")

        seen_urls = set()
        links_to_process = []

        # Strategy 1: h2/h3 containing <a> tags (Polygon, RPS, most sites)
        for h in soup.find_all(["h2", "h3"]):
            a_tag = h.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"]
            if not href.startswith("http"):
                href = urljoin(url, href)
            if domain and domain not in href:
                continue
            title = a_tag.get_text(strip=True)
            if title and len(title) > 15 and href not in seen_urls:
                seen_urls.add(href)
                links_to_process.append((href, title))

        # Strategy 2: <a> with class "block" or long text (Kotaku style)
        if len(links_to_process) < 5:
            skip_patterns = ["/latest", "/tag/", "/author/", "/about", "/search", "/users/"]
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if not href.startswith("http"):
                    href = urljoin(url, href)
                if domain and domain not in href:
                    continue
                if any(p in href for p in skip_patterns):
                    continue
                title = a_tag.get_text(strip=True)
                # Clean category prefixes
                for prefix in ["Culture", "News", "Opinion", "Entertainment", "Tips & Guides", "Commentary", "Review"]:
                    if title.startswith(prefix):
                        title = title[len(prefix):].strip()
                if title and len(title) > 25 and href not in seen_urls:
                    seen_urls.add(href)
                    links_to_process.append((href, title))
                    if len(links_to_process) >= 30:
                        break

        logger.info("%s: found %d article links on homepage", name, len(links_to_process))

        for link, title in links_to_process[:30]:
            if news_exists(link) or _is_junk_url(link):
                continue
            time.sleep(1)
            h1, description, plain_text, published_at, image_count = _fetch_article(link)
            if _is_too_old(published_at):  # свежесть: не тащим старьё в базу
                continue
            final_title = h1 if h1 and len(h1) > 15 else title
            news_id = insert_news(
                source=name, url=link, title=final_title,
                h1=h1, description=description,
                plain_text=plain_text, published_at=published_at,
                image_count=image_count,
            )
            if news_id:
                count += 1

    except Exception as e:
        logger.error("Error parsing %s homepage: %s", name, e)
        hard_err = e

    # Also try RSS as supplement
    rss_url = source.get("rss_url")
    if rss_url:
        try:
            from parsers.rss_parser import parse_rss_source
            rss_source = {**source, "type": "rss", "url": rss_url}
            rss_count = parse_rss_source(rss_source) or 0
            count += rss_count
            if rss_count:
                logger.info("%s RSS supplement: %d new articles", name, rss_count)
        except Exception as e:
            logger.debug("%s RSS fallback failed: %s", name, e)

    if count == 0 and hard_err is not None:
        _record_failure(name, hard_err)
        return None
    logger.info("Parsed %s (homepage+RSS): %d new articles", name, count)
    return count


def parse_sitemap_source(source: dict) -> int:
    """Парсит sitemap XML, берёт свежие URL (за последние 30 дней) и загружает статьи."""
    name = source["name"]
    url = source["url"]
    url_filter = source.get("url_filter", "")
    max_age_days = source.get("max_age_days", 30)
    count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    try:
        resp = fetch_with_retry(url)

        root = ElementTree.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # Проверяем: это sitemapindex или обычный sitemap?
        if root.tag.endswith("sitemapindex"):
            # Это индекс — берём последние 2 sitemap-файла
            sitemaps = root.findall("sm:sitemap", ns)
            sitemap_urls = [s.find("sm:loc", ns).text for s in sitemaps if s.find("sm:loc", ns) is not None]
            # Берём последние 2
            for sm_url in sitemap_urls[-2:]:
                count += _parse_single_sitemap(name, sm_url, url_filter, cutoff)
        else:
            # Обычный sitemap
            count = _parse_single_sitemap_from_root(name, root, ns, url_filter, cutoff)

    except Exception as e:
        logger.error("Error parsing sitemap %s: %s", name, e)
        _record_failure(name, e)
        return None

    logger.info("Parsed %s (sitemap): %d new articles", name, count)
    return count


def _parse_single_sitemap(name: str, sm_url: str, url_filter: str, cutoff: datetime) -> int:
    """Загружает один sitemap и парсит его."""
    try:
        resp = fetch_with_retry(sm_url)
        root = ElementTree.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        return _parse_single_sitemap_from_root(name, root, ns, url_filter, cutoff)
    except Exception as e:
        logger.error("Error loading sitemap %s: %s", sm_url, e)
        return 0


def _parse_single_sitemap_from_root(name: str, root, ns: dict, url_filter: str, cutoff: datetime) -> int:
    """Парсит URL из sitemap XML root."""
    count = 0
    urls = root.findall("sm:url", ns)

    # Собираем свежие URL
    fresh_urls = []
    for u in urls:
        loc = u.find("sm:loc", ns)
        lastmod = u.find("sm:lastmod", ns)
        if loc is None:
            continue
        link = loc.text.strip()

        # Фильтр по URL (например только /news/)
        if url_filter and url_filter not in link:
            continue

        # Фильтр по дате
        if lastmod is not None and lastmod.text:
            try:
                dt = datetime.fromisoformat(lastmod.text.strip())
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
                fresh_urls.append((link, lastmod.text.strip()))
            except ValueError:
                fresh_urls.append((link, ""))
        else:
            fresh_urls.append((link, ""))

    # Сортируем по дате (новые первыми) и берём до 20
    fresh_urls.sort(key=lambda x: x[1], reverse=True)

    for link, published_at in fresh_urls[:20]:
        if news_exists(link) or _is_junk_url(link):
            continue

        time.sleep(1)
        h1, description, plain_text, page_date, image_count = _fetch_article(link)

        if not h1 or len(h1) < 10:
            continue

        # Приоритет: дата из sitemap, иначе из HTML страницы
        final_date = published_at or page_date
        if _is_too_old(final_date):  # свежесть: не тащим старьё в базу
            continue

        nid = insert_news(
            source=name, url=link, title=h1,
            h1=h1, description=description,
            plain_text=plain_text, published_at=final_date,
            image_count=image_count,
        )
        if nid:
            count += 1

    return count


_VISIBLE_DATE_SELECTORS = [
    ".published-date", ".post-published", ".post__date", ".post-date",
    ".entry-date", ".article-date", ".wp-block-post-date",
    "div.blog__post__item__date", "p.text-body-small",
    "[class*='publish']", "[class*='posted']", "article.post .ath.du p",
]
_VISIBLE_DATE_RE = re.compile(
    r"\d{1,2}\s+[A-Za-zА-Яа-яёЁ]{3,}\.?\s+20\d{2}"        # D Month YYYY
    r"|[A-Za-zА-Яа-яёЁ]{3,}\.?\s+\d{1,2},?\s+20\d{2}"     # Month D, YYYY
    r"|\d{1,2}\.\d{1,2}\.20\d{2}"                          # DD.MM.YYYY
    r"|20\d{2}-\d{1,2}-\d{1,2}",                           # YYYY-MM-DD
)


def _jsonld_date(node):
    """Рекурсивно ищет дату публикации в JSON-LD. Возвращает datePublished
    (приоритет, у Article-узлов), иначе dateModified. Игнорирует _updatedAt и пр."""
    published = [None]
    modified = [None]

    def walk(n):
        if isinstance(n, dict):
            for k in ("datePublished", "dateCreated", "datePosted"):
                v = n.get(k)
                if isinstance(v, str) and v.strip() and not published[0]:
                    published[0] = v.strip()
            v = n.get("dateModified")
            if isinstance(v, str) and v.strip() and not modified[0]:
                modified[0] = v.strip()
            for val in n.values():
                walk(val)
        elif isinstance(n, list):
            for it in n:
                walk(it)

    walk(node)
    return published[0] or modified[0]


def _extract_publish_date(soup) -> str:
    """Дата публикации из HTML. Приоритет: JSON-LD datePublished → meta →
    <time datetime> → видимые date-селекторы → regex видимой даты. '' если нет."""
    from storage.database import _parse_date_loose

    # 1. JSON-LD (рекурсивно, без захвата произвольных ISO/_updatedAt)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = _jsonld_date(json.loads(script.string or ""))
            if d:
                return d
        except Exception:
            pass

    # 2. Meta — только реальные «опубликовано» (modified как последний шанс)
    for meta_name in ("article:published_time", "og:article:published_time",
                      "parsely-pub-date", "datePublished", "date", "pubdate",
                      "DC.date.issued", "sailthru.date"):
        tag = soup.find("meta", attrs={"property": meta_name}) or \
              soup.find("meta", attrs={"name": meta_name})
        if tag and tag.get("content", "").strip():
            return tag["content"].strip()

    # 3. <time datetime>
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag and time_tag.get("datetime", "").strip():
        return time_tag["datetime"].strip()

    # 4. Видимые date-селекторы (если парсятся)
    for sel in _VISIBLE_DATE_SELECTORS:
        try:
            for el in soup.select(sel)[:3]:
                txt = el.get_text(" ", strip=True)
                if txt and _parse_date_loose(txt):
                    return txt
        except Exception:
            pass

    # 5. Regex видимой даты в шапке статьи (ограниченно, чтобы не ловить «© 2020»)
    container = soup.find("article") or soup.find("main") or soup.body
    if container:
        txt = container.get_text(" ", strip=True)[:800]
        m = _VISIBLE_DATE_RE.search(txt)
        if m and _parse_date_loose(m.group(0)):
            return m.group(0)

    # 6. fallback на modified из meta (если совсем ничего)
    tag = soup.find("meta", attrs={"property": "article:modified_time"}) or \
          soup.find("meta", attrs={"name": "og:updated_time"})
    if tag and tag.get("content", "").strip():
        return tag["content"].strip()

    return ""


_JUNK_TAGS = ["script", "style", "nav", "footer", "header", "aside",
              "figure", "figcaption", "form", "button", "svg", "noscript", "iframe"]
_JUNK_CLASS_KW = ["share", "social", "bookmark", "comment", "sidebar", "related", "newsletter", "promo", "ad-",
                  # consent/cookie/подписные оверлеи: t3n (c-consent-overlay/consent-error/-initial)
                  # тянул consent-баннер + targetvideo-плеер прямо в тело статьи → мусор в биграммах/quality.
                  "consent", "cookie", "cmp", "gdpr", "paywall", "subscribe", "signup"]


def _clean_element(el) -> str:
    """Очищает элемент от мусора и возвращает текст."""
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


def _count_content_images(el) -> int:
    """Считает значимые <img> в теле статьи (без трекинг-пикселей)."""
    n = 0
    for img in el.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").lower()
        if not src or src.startswith("data:"):
            continue
        try:
            w = int(img.get("width", "0") or 0)
            h = int(img.get("height", "0") or 0)
        except ValueError:
            w = h = 0
        if 0 < w <= 2 or 0 < h <= 2:  # трекинг-пиксели 1x1
            continue
        n += 1
    return n


def _extract_body_text(soup) -> tuple[str, int]:
    """Извлекает основной текст статьи + число картинок. Пробует несколько селекторов."""
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


def _fetch_article(url: str) -> tuple[str, str, str, str, int]:
    """Загружает статью: h1, description, plain_text, published_at, image_count."""
    try:
        resp = fetch_with_retry(url)
        html_text = resp.text[:512_000]  # Limit to 500KB to prevent OOM
        del resp
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

        published_at = _extract_publish_date(soup)
        if not published_at:
            published_at = _date_from_url(url)  # последний фолбэк — дата из URL

        # Умный поиск текста по множеству селекторов
        plain_text, image_count = _extract_body_text(soup)

        del soup  # free lxml tree immediately

        # Fallback: если text пуст, используем description
        if not plain_text and description:
            plain_text = description
            logger.debug("Text recovery for %s: using description (%d chars)", url, len(description))

        return h1, description, plain_text, published_at, image_count
    except Exception as e:
        logger.warning("Failed to fetch article %s: %s", url, e)
        return "", "", "", "", 0
