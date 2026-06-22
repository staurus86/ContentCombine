import base64
import json
import logging
import time
import os
import threading

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

# ─── Client & caching ───────────────────────────────────────────────

_client = None
_client_created_at = 0
_CLIENT_TTL = config.SHEETS_CLIENT_TTL  # refresh client every 50 min (tokens expire at 60)

_worksheet_cache = {}  # {tab_name: worksheet}
_worksheet_cache_at = 0
_WS_CACHE_TTL = 300  # cache worksheets for 5 min

_url_cache = {}  # {tab_name: set(urls)}
_url_cache_at = {}  # {tab_name: timestamp}
_URL_CACHE_TTL = 600  # cache URLs for 10 min (prevent cache churn during batch writes)

_rate_lock = threading.Lock()
_last_api_call = 0
_MIN_INTERVAL = config.SHEETS_MIN_API_INTERVAL  # min interval between API calls (50 req/min safe)


def _rate_limit():
    """Enforce minimum interval between Google Sheets API calls."""
    global _last_api_call
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_api_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_api_call = time.time()


def get_sheets_config_error() -> str:
    """Return human-readable config error or empty string when Sheets is configured."""
    if not config.GOOGLE_SHEETS_ID:
        return "GOOGLE_SHEETS_ID not set"
    if not config.GOOGLE_SERVICE_ACCOUNT_JSON:
        return "GOOGLE_SERVICE_ACCOUNT_JSON not set"
    return ""


def _get_client():
    global _client, _client_created_at
    now = time.time()
    if _client is not None and (now - _client_created_at) < _CLIENT_TTL:
        return _client

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    config_error = get_sheets_config_error()
    if config_error:
        logger.error(config_error)
        return None
    sa_json = config.GOOGLE_SERVICE_ACCOUNT_JSON

    try:
        decoded = base64.b64decode(sa_json)
        creds_data = json.loads(decoded)
    except Exception:
        if os.path.exists(sa_json):
            with open(sa_json) as f:
                creds_data = json.load(f)
        else:
            creds_data = json.loads(sa_json)

    creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
    _client = gspread.authorize(creds)
    _client_created_at = now
    # Invalidate worksheet cache on re-auth
    _worksheet_cache.clear()
    logger.info("Google Sheets client (re)authorized")
    return _client


def _retry_api(func, max_retries=5):
    """Retry gspread API call with exponential backoff on rate limit / server errors.

    On 401/403 (auth expired mid-export) the client cache is invalidated so the
    next _get_client() call re-authorizes automatically, then the call is retried
    once before giving up.
    """
    global _client_created_at
    _auth_retried = False

    for attempt in range(max_retries):
        _rate_limit()
        try:
            return func()
        except gspread.exceptions.APIError as e:
            code = e.response.status_code if hasattr(e, 'response') else 0

            # Auth expired — refresh credentials and retry once
            if code in (401, 403) and not _auth_retried:
                logger.warning(
                    "Sheets auth error %d on attempt %d — refreshing credentials and retrying",
                    code, attempt + 1,
                )
                _auth_retried = True
                _client_created_at = 0   # invalidate TTL → _get_client() will re-auth
                _worksheet_cache.clear()  # stale worksheet handles are invalid after re-auth
                time.sleep(1)
                continue  # retry with fresh client on next loop iteration

            if code in (429, 500, 503) and attempt < max_retries - 1:
                # Aggressive backoff for 429: 5s, 15s, 45s, 120s
                if code == 429:
                    wait = min(5 * (3 ** attempt), 120)
                else:
                    wait = (2 ** attempt) * 2  # 2s, 4s, 8s, 16s
                logger.warning("Sheets API %d, retry in %ds (attempt %d/%d)", code, wait, attempt + 1, max_retries)
                time.sleep(wait)
                if code == 429 and attempt >= 1:
                    _client_created_at = 0
                continue
            raise
        except Exception as e:
            if "timed out" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def _get_worksheet(tab_name: str, headers: list = None):
    """Get cached worksheet, create if needed.

    If the cached worksheet handle is stale (e.g. after a token refresh), the
    cache is cleared and a single transparent retry is performed so callers
    never need to handle auth errors themselves.
    """
    global _worksheet_cache_at, _client_created_at

    def _fetch():
        global _worksheet_cache_at
        now = time.time()
        # Return cached worksheet only if cache is fresh
        if tab_name in _worksheet_cache and (now - _worksheet_cache_at) < _WS_CACHE_TTL:
            return _worksheet_cache[tab_name]

        client = _get_client()
        if not client:
            return None

        spreadsheet = _retry_api(lambda: client.open_by_key(config.GOOGLE_SHEETS_ID))
        try:
            ws = _retry_api(lambda: spreadsheet.worksheet(tab_name))
        except gspread.exceptions.WorksheetNotFound:
            if headers:
                ws = _retry_api(lambda: spreadsheet.add_worksheet(
                    title=tab_name, rows=1000, cols=len(headers)))
                _retry_api(lambda: ws.insert_row(headers, index=1, value_input_option="USER_ENTERED"))
                logger.info("Created worksheet '%s' with headers", tab_name)
            else:
                return None

        # Ensure headers are present
        if headers:
            first_row = _retry_api(lambda: ws.row_values(1))
            if not first_row or first_row[0] != headers[0]:
                _retry_api(lambda: ws.insert_row(headers, index=1, value_input_option="USER_ENTERED"))

        _worksheet_cache[tab_name] = ws
        _worksheet_cache_at = time.time()
        return ws

    try:
        return _fetch()
    except gspread.exceptions.APIError as e:
        code = e.response.status_code if hasattr(e, 'response') else 0
        if code in (401, 403):
            logger.warning(
                "Sheets auth error %d getting worksheet '%s' — refreshing and retrying",
                code, tab_name,
            )
            _client_created_at = 0
            _worksheet_cache.clear()
            try:
                return _fetch()
            except Exception as e2:
                logger.error("Failed to get worksheet '%s' after re-auth: %s", tab_name, e2)
                return None
        logger.error("Failed to get worksheet '%s': %s", tab_name, e)
        return None
    except Exception as e:
        logger.error("Failed to get worksheet '%s': %s", tab_name, e)
        return None


def _get_cached_urls(ws, col_index: int, tab_name: str) -> set:
    """Get cached URL set for dedup, refresh periodically."""
    now = time.time()
    cache_time = _url_cache_at.get(tab_name, 0)
    if tab_name in _url_cache and (now - cache_time) < _URL_CACHE_TTL:
        return _url_cache[tab_name]

    try:
        urls = set(_retry_api(lambda: ws.col_values(col_index)))
        _url_cache[tab_name] = urls
        _url_cache_at[tab_name] = now
        return urls
    except Exception as e:
        logger.warning("Failed to load URLs for dedup (%s): %s", tab_name, e)
        return set()


def _append_row(ws, row: list, tab_name: str, news_url: str = "") -> int | None:
    """Append row and update caches. Returns estimated row number."""
    _retry_api(lambda: ws.append_row(row, value_input_option="USER_ENTERED", table_range="A1"))
    # Update URL cache
    if news_url and tab_name in _url_cache:
        _url_cache[tab_name].add(news_url)
    # Estimate row number from URL cache size (avoid extra API call)
    return len(_url_cache.get(tab_name, set())) + 1


def _append_rows_batch(ws, rows: list[list], tab_name: str, urls: list[str] = None) -> int:
    """Append multiple rows at once. Returns count of rows written."""
    if not rows:
        return 0
    _retry_api(lambda: ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1"))
    # Update URL cache
    if urls and tab_name in _url_cache:
        _url_cache[tab_name].update(u for u in urls if u)
    return len(rows)


# ─── Helper ──────────────────────────────────────────────────────────

def _safe_json_loads(val, default):
    """Безопасный json.loads: обрабатывает None, пустые строки, уже-распарсенные объекты."""
    if val is None or val == "":
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def _format_viral_triggers(analysis: dict | None) -> str:
    """Форматирует виральные триггеры из news_analysis для Sheets."""
    if not analysis:
        return ""
    try:
        triggers_raw = analysis.get("viral_data", "")
        if not triggers_raw:
            return ""
        triggers = json.loads(triggers_raw) if isinstance(triggers_raw, str) else triggers_raw
        if not isinstance(triggers, list):
            return ""
        return ", ".join(f"{t.get('label', '?')}({t.get('weight', 0)})" for t in triggers[:10])
    except Exception:
        return ""


def _format_viral_triggers_from_checks(checks: dict) -> str:
    """Форматирует виральные триггеры из check_results для Sheets."""
    viral = checks.get("viral", {})
    triggers = viral.get("triggers", [])
    if not triggers:
        return ""
    return ", ".join(f"{t.get('label', '?')}({t.get('weight', 0)})" for t in triggers[:10])


def _pick_not_ready_date(news: dict) -> str:
    """Prefer published date, then parsed date for NotReady exports."""
    return (
        news.get("published_at")
        or news.get("parsed_at")
        or ""
    )


def _pick_not_ready_text(news: dict) -> str:
    """Prefer article text, then description for NotReady exports."""
    text = (
        news.get("plain_text")
        or news.get("description")
        or ""
    )
    return str(text)[:1000]


# ─── Headers ─────────────────────────────────────────────────────────

HEADERS = [
    "Дата парсинга", "Источник (URL)", "Title", "H1", "Description",
    "Топ биграммы", "Частота (Keys.so)", "Google Trends RU",
    "Google Trends US", "Рекомендация LLM", "Прогноз трендовости",
    "Похожие запросы Keys.so", "Объединить с", "Статус", "Plain Text",
]

HEADERS_READY = [
    "Дата", "Источник", "Оригинал заголовок", "Рерайт заголовок", "Рерайт текст",
    "Описание (лента)", "Meta Title", "Meta Description", "Теги", "Скор", "Вирал",
    "Вирал триггеры", "Keys.so частота", "Тренды RU", "LLM рекомендация",
    "Прогноз", "URL оригинала",
]

HEADERS_NOT_READY = [
    "Дата", "Источник", "Заголовок", "Скор", "Качество", "Релевантность",
    "Свежесть (ч)", "Вирал", "Тригеры", "Тон", "Теги", "Entities",
    "Headline скор", "Momentum", "URL", "Описание",
]

HEADERS_STORYLINES = [
    "Сюжет", "Фаза", "Кол-во новостей", "Источники", "Ср. скор", "Макс. вирал",
    "Игры", "Виральные триггеры", "Новость", "Источник новости", "Скор новости", "Дата", "URL",
]


# ─── Public API ──────────────────────────────────────────────────────

def setup_headers():
    """Создаёт заголовки в первой строке, если их нет."""
    _get_worksheet(config.SHEETS_TAB, HEADERS)


def write_news_row(news: dict, analysis: dict) -> int | None:
    """Записывает строку новости в Лист1. Возвращает номер строки."""
    ws = _get_worksheet(config.SHEETS_TAB, HEADERS)
    if not ws:
        return None

    try:
        news_url = news.get("url") or ""
        if news_url:
            existing = _get_cached_urls(ws, 2, config.SHEETS_TAB)  # col B = URL
            if news_url in existing:
                logger.info("Skipped duplicate in Sheets: %s", news_url[:80])
                return -1

        bigrams = _safe_json_loads(analysis.get("bigrams"), [])
        bigrams_str = ", ".join(
            b[0] if isinstance(b, list) else str(b)
            for b in bigrams
        )

        keyso_data = _safe_json_loads(analysis.get("keyso_data"), {})
        trends_data = _safe_json_loads(analysis.get("trends_data"), {})

        row = [
            news.get("parsed_at") or "",                        # A
            news_url,                                           # B
            news.get("title") or "",                            # C
            news.get("h1") or "",                               # D
            news.get("description") or "",                      # E
            bigrams_str,                                        # F
            str(keyso_data.get("freq", "")),                    # G
            str(trends_data.get("RU", "")),                     # H
            str(trends_data.get("US", "")),                     # I
            analysis.get("llm_recommendation") or "",           # J
            analysis.get("llm_trend_forecast") or "",           # K
            ", ".join(keyso_data.get("similar", [])) if isinstance(keyso_data.get("similar"), list) else str(keyso_data.get("similar", "")),  # L
            analysis.get("llm_merged_with") or "",              # M
            news.get("status") or "new",                        # N
            (news.get("plain_text") or "")[:1000],              # O
        ]

        row_num = _append_row(ws, row, config.SHEETS_TAB, news_url)
        logger.info("Written to Sheets row %s: %s", row_num, (news.get("title") or "")[:50])
        return row_num

    except Exception as e:
        logger.error("Sheets write error: %s", e)
        return None


def write_ready_row(news: dict, analysis: dict, rewrite: dict) -> int | None:
    """Записывает переписанную новость в вкладку Ready."""
    tab_name = getattr(config, "SHEETS_TAB_READY", "Ready")
    ws = _get_worksheet(tab_name, HEADERS_READY)
    if not ws:
        return None

    try:
        news_url = news.get("url") or ""
        if news_url:
            existing = _get_cached_urls(ws, 17, tab_name)  # col Q = URL
            if news_url in existing:
                logger.info("Skipped duplicate in Ready: %s", news_url[:80])
                return -1

        keyso_data = _safe_json_loads(analysis.get("keyso_data") if analysis else None, {})
        trends_data = _safe_json_loads(analysis.get("trends_data") if analysis else None, {})

        tags = ""
        try:
            rewrite_tags = rewrite.get("tags", [])
            if rewrite_tags and isinstance(rewrite_tags, list):
                tags = ", ".join(str(t) for t in rewrite_tags)
            elif analysis:
                tags_list = _safe_json_loads(analysis.get("tags"), [])
                tags = ", ".join(tags_list) if isinstance(tags_list, list) else str(tags_list)
        except Exception:
            pass

        row = [
            news.get("parsed_at") or "",                             # A
            news.get("source") or "",                                # B
            news.get("title") or "",                                 # C
            rewrite.get("title") or "",                              # D
            (rewrite.get("text") or "")[:5000],                      # E
            (rewrite.get("feed_description") or "")[:500],           # F
            rewrite.get("seo_title", rewrite.get("meta_title", "")) or "",  # G
            rewrite.get("seo_description", rewrite.get("meta_description", "")) or "",  # H
            tags,                                                    # I
            str(analysis.get("total_score", "") if analysis else ""),  # J
            str(analysis.get("viral_score", "") if analysis else ""),  # K
            _format_viral_triggers(analysis),                        # L
            str(keyso_data.get("freq", "")),                         # M
            str(trends_data.get("RU", "")),                          # N
            (analysis.get("llm_recommendation") or "") if analysis else "",  # O
            (analysis.get("llm_trend_forecast") or "") if analysis else "",  # P
            news_url,                                                # Q
        ]

        row_num = _append_row(ws, row, tab_name, news_url)
        logger.info("Written to Ready row %s: %s", row_num, (news.get("title") or "")[:50])
        return row_num

    except Exception as e:
        logger.error("Ready sheet write error: %s", e)
        return None


def write_not_ready_row(news: dict, check_results: dict) -> int | None:
    """Записывает новость с локальным анализом в вкладку NotReady."""
    tab_name = getattr(config, "SHEETS_TAB_NOT_READY", "NotReady")
    ws = _get_worksheet(tab_name, HEADERS_NOT_READY)
    if not ws:
        logger.error("NotReady: worksheet not available")
        return None

    try:
        news_url = news.get("url") or ""
        if news_url:
            existing = _get_cached_urls(ws, 15, tab_name)  # col O = URL
            if news_url in existing:
                logger.info("Skipped duplicate in NotReady: %s", news_url[:80])
                return -1

        checks = check_results.get("checks") or {}

        # Tags: list[str], list[dict], or JSON string
        tags_raw = check_results.get("tags") or []
        if isinstance(tags_raw, str):
            tags_raw = _safe_json_loads(tags_raw, [])
        tags_parts = []
        for t in (tags_raw if isinstance(tags_raw, list) else []):
            if isinstance(t, dict):
                tags_parts.append(t.get("label") or t.get("id") or "")
            elif isinstance(t, str):
                tags_parts.append(t)
        tags_str = ", ".join(filter(None, tags_parts))

        # Entities: list[dict], list[str], or JSON string
        entities_raw = check_results.get("game_entities") or []
        if isinstance(entities_raw, str):
            entities_raw = _safe_json_loads(entities_raw, [])
        ent_names = []
        for e in (entities_raw if isinstance(entities_raw, list) else []):
            if isinstance(e, dict):
                ent_names.append(e.get("name") or "")
            elif isinstance(e, str):
                ent_names.append(e)
        entities_str = ", ".join(filter(None, ent_names[:10]))

        freshness = checks.get("freshness") or {}
        age_h = freshness.get("age_hours", -1)
        age_str = f"{age_h:.1f}" if isinstance(age_h, (int, float)) and age_h >= 0 else "?"

        row = [
            _pick_not_ready_date(news),                                # A
            news.get("source") or "",                                  # B
            news.get("title") or "",                                   # C
            str(check_results.get("total_score") or 0),                # D
            str((checks.get("quality") or {}).get("score", 0)),        # E
            str((checks.get("relevance") or {}).get("score", 0)),      # F
            age_str,                                                   # G
            str((checks.get("viral") or {}).get("score", 0)),          # H
            _format_viral_triggers_from_checks(checks),                # I
            (check_results.get("sentiment") or {}).get("label", "neutral"),  # J
            tags_str,                                                  # K
            entities_str,                                              # L
            str((check_results.get("headline") or {}).get("score", 0)),  # M
            str((check_results.get("momentum") or {}).get("score", 0)),  # N
            news_url,                                                  # O
            _pick_not_ready_text(news),                                # P
        ]

        row_num = _append_row(ws, row, tab_name, news_url)
        logger.info("Written to NotReady row %s: %s", row_num, (news.get("title") or "")[:50])
        return row_num

    except Exception as e:
        logger.error("NotReady write error for '%s': %s", (news.get("title") or "")[:50], e, exc_info=True)
        return None


def _build_not_ready_row(news: dict, check_results: dict) -> tuple[list, str]:
    """Build a NotReady row without writing. Returns (row_data, news_url)."""
    checks = check_results.get("checks") or {}
    news_url = news.get("url") or ""

    tags_raw = check_results.get("tags") or []
    if isinstance(tags_raw, str):
        tags_raw = _safe_json_loads(tags_raw, [])
    tags_parts = []
    for t in (tags_raw if isinstance(tags_raw, list) else []):
        if isinstance(t, dict):
            tags_parts.append(t.get("label") or t.get("id") or "")
        elif isinstance(t, str):
            tags_parts.append(t)
    tags_str = ", ".join(filter(None, tags_parts))

    entities_raw = check_results.get("game_entities") or []
    if isinstance(entities_raw, str):
        entities_raw = _safe_json_loads(entities_raw, [])
    ent_names = []
    for e in (entities_raw if isinstance(entities_raw, list) else []):
        if isinstance(e, dict):
            ent_names.append(e.get("name") or "")
        elif isinstance(e, str):
            ent_names.append(e)
    entities_str = ", ".join(filter(None, ent_names[:10]))

    freshness = checks.get("freshness") or {}
    age_h = freshness.get("age_hours", -1)
    age_str = f"{age_h:.1f}" if isinstance(age_h, (int, float)) and age_h >= 0 else "?"

    row = [
        _pick_not_ready_date(news),
        news.get("source") or "",
        news.get("title") or "",
        str(check_results.get("total_score") or 0),
        str((checks.get("quality") or {}).get("score", 0)),
        str((checks.get("relevance") or {}).get("score", 0)),
        age_str,
        str((checks.get("viral") or {}).get("score", 0)),
        _format_viral_triggers_from_checks(checks),
        (check_results.get("sentiment") or {}).get("label", "neutral"),
        tags_str,
        entities_str,
        str((check_results.get("headline") or {}).get("score", 0)),
        str((check_results.get("momentum") or {}).get("score", 0)),
        news_url,
        _pick_not_ready_text(news),
    ]
    return row, news_url


def write_not_ready_batch(items: list[tuple[dict, dict]]) -> dict:
    """Batch write to NotReady tab. items = [(news, check_results), ...].

    Returns {"written": int, "skipped": int, "errors": int}.
    """
    tab_name = getattr(config, "SHEETS_TAB_NOT_READY", "NotReady")
    ws = _get_worksheet(tab_name, HEADERS_NOT_READY)
    if not ws:
        logger.error("NotReady batch: worksheet not available")
        return {"written": 0, "skipped": 0, "errors": len(items)}

    # Pre-warm URL cache once for entire batch
    existing_urls = _get_cached_urls(ws, 15, tab_name)

    rows_to_write = []
    urls_to_write = []
    skipped = 0
    errors = 0

    for news, check_results in items:
        try:
            row, news_url = _build_not_ready_row(news, check_results)
            if news_url and news_url in existing_urls:
                skipped += 1
                continue
            rows_to_write.append(row)
            urls_to_write.append(news_url)
            existing_urls.add(news_url)  # prevent intra-batch dupes
        except Exception as e:
            errors += 1
            logger.error("NotReady batch row build error: %s", e)

    # Write in sub-batches
    BATCH_SIZE = config.SHEETS_BATCH_SIZE
    written = 0
    for start in range(0, len(rows_to_write), BATCH_SIZE):
        chunk = rows_to_write[start:start + BATCH_SIZE]
        chunk_urls = urls_to_write[start:start + BATCH_SIZE]
        try:
            cnt = _append_rows_batch(ws, chunk, tab_name, chunk_urls)
            written += cnt
            logger.info("NotReady batch: wrote %d rows (%d/%d total)",
                        cnt, written, len(rows_to_write))
            # Pause between sub-batches to stay under rate limits
            if start + BATCH_SIZE < len(rows_to_write):
                time.sleep(3)
        except Exception as e:
            errors += len(chunk)
            logger.error("NotReady batch write error at rows %d-%d: %s",
                         start, start + len(chunk), e)
            # On error, try smaller chunks (one by one)
            time.sleep(10)
            for j, (row, url) in enumerate(zip(chunk, chunk_urls)):
                try:
                    _append_row(ws, row, tab_name, url)
                    written += 1
                    errors -= 1
                    time.sleep(2)
                except Exception as e2:
                    logger.error("NotReady single-row fallback failed: %s", e2)

    logger.info("NotReady batch complete: %d written, %d skipped, %d errors",
                written, skipped, errors)
    return {"written": written, "skipped": skipped, "errors": errors}


def write_storylines(storylines: list[dict]) -> dict:
    """Export storylines to 'Сюжеты' tab in Google Sheets.

    Each storyline is expanded: one header row per cluster + one row per member news.
    Returns {"written": int, "storylines": int}.
    """
    tab_name = "Сюжеты"
    ws = _get_worksheet(tab_name, HEADERS_STORYLINES)
    if not ws:
        return {"status": "error", "message": "Sheets не доступен"}

    # Clear old data (keep headers)
    try:
        _retry_api(lambda: ws.clear())
        _retry_api(lambda: ws.insert_row(HEADERS_STORYLINES, index=1, value_input_option="USER_ENTERED"))
    except Exception as e:
        logger.error("Storylines: failed to clear tab: %s", e)
        return {"status": "error", "message": str(e)}

    rows = []
    phase_labels = {"trending": "Тренд", "developing": "Развивается", "emerging": "Зарождается"}

    for idx, s in enumerate(storylines, 1):
        cluster_name = f"Сюжет #{idx}"
        phase = phase_labels.get(s.get("phase", ""), s.get("phase", ""))
        sources = ", ".join(s.get("sources", []))
        games = ", ".join(s.get("top_games", []))
        triggers = ", ".join(s.get("top_triggers", []))
        count = s.get("count", 0)
        avg_score = s.get("avg_score", 0)
        max_viral = s.get("max_viral", 0)

        members = s.get("members", [])
        if not members:
            # Storyline without members — single summary row
            rows.append([
                cluster_name, phase, count, sources, avg_score, max_viral,
                games, triggers, "", "", "", "", "",
            ])
        else:
            # First member row includes cluster info
            m = members[0]
            rows.append([
                cluster_name, phase, count, sources, avg_score, max_viral,
                games, triggers,
                m.get("title", ""), m.get("source", ""), m.get("total_score", 0),
                m.get("published_at", "")[:16], m.get("url", ""),
            ])
            # Remaining members — only news columns
            for m in members[1:]:
                rows.append([
                    "", "", "", "", "", "", "", "",
                    m.get("title", ""), m.get("source", ""), m.get("total_score", 0),
                    m.get("published_at", "")[:16], m.get("url", ""),
                ])

    # Write in batches
    written = 0
    BATCH = 50
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        try:
            _append_rows_batch(ws, chunk, tab_name)
            written += len(chunk)
            if start + BATCH < len(rows):
                time.sleep(3)
        except Exception as e:
            logger.error("Storylines batch write error: %s", e)

    logger.info("Storylines export: %d rows written, %d storylines", written, len(storylines))
    return {"status": "ok", "written": written, "storylines": len(storylines)}


def write_deleted_batch(items: list[dict]) -> dict:
    """Export deleted news to 'Удалённые' tab in Google Sheets.

    items = list of dicts with news + analysis fields.
    Returns {"written": int, "skipped": int}.
    """
    tab_name = "Удалённые"
    ws = _get_worksheet(tab_name, HEADERS_NOT_READY)
    if not ws:
        return {"written": 0, "skipped": 0, "errors": len(items)}

    existing_urls = _get_cached_urls(ws, 15, tab_name)  # col O = URL
    rows = []
    skipped = 0

    for item in items:
        url = item.get("url") or ""
        if url in existing_urls:
            skipped += 1
            continue

        tags_raw = item.get("tags_data") or "[]"
        if isinstance(tags_raw, str):
            tags_raw = _safe_json_loads(tags_raw, [])
        tags_str = ", ".join(
            (t.get("label") or t.get("id") or t) if isinstance(t, dict) else str(t)
            for t in (tags_raw if isinstance(tags_raw, list) else [])
        )

        entities_raw = item.get("entity_names") or "[]"
        if isinstance(entities_raw, str):
            entities_raw = _safe_json_loads(entities_raw, [])
        entities_str = ", ".join(
            (e.get("name") or e) if isinstance(e, dict) else str(e)
            for e in (entities_raw if isinstance(entities_raw, list) else [])[:10]
        )

        viral_raw = item.get("viral_data") or "[]"
        if isinstance(viral_raw, str):
            viral_raw = _safe_json_loads(viral_raw, [])
        viral_str = ", ".join(
            (v.get("label") or v.get("trigger") or str(v)) if isinstance(v, dict) else str(v)
            for v in (viral_raw if isinstance(viral_raw, list) else [])
        )

        age_h = item.get("freshness_hours", -1)
        age_str = f"{age_h:.1f}" if isinstance(age_h, (int, float)) and age_h >= 0 else "?"

        rows.append([
            item.get("parsed_at") or "",
            item.get("source") or "",
            item.get("title") or "",
            str(item.get("total_score") or 0),
            str(item.get("quality_score") or 0),
            str(item.get("relevance_score") or 0),
            age_str,
            str(item.get("viral_score") or 0),
            viral_str,
            item.get("sentiment_label") or "neutral",
            tags_str,
            entities_str,
            str(item.get("headline_score") or 0),
            str(item.get("momentum_score") or 0),
            url,
            (item.get("description") or "")[:500],
        ])

    if not rows:
        return {"written": 0, "skipped": skipped}

    written = 0
    BATCH = 50
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        try:
            _append_rows_batch(ws, chunk, tab_name)
            written += len(chunk)
            if start + BATCH < len(rows):
                time.sleep(3)
        except Exception as e:
            logger.error("Deleted export batch error: %s", e)

    logger.info("Deleted export: %d written, %d skipped", written, skipped)
    return {"written": written, "skipped": skipped}
