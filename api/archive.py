"""
Архив новостей: текст статьи старше ARCHIVE_AGE_DAYS уезжает в Google Sheets,
после подтверждённой записи обнуляется в базе.

В базе остаётся вся остальная строка — заголовок, URL, источник, даты, статус,
флаги. Аналитика и тренды считаются по ним и по news_analysis, plain_text не
читают, поэтому чистка текста их не задевает.
"""

import logging
from datetime import datetime, timezone, timedelta

from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

# Колонки, которые уезжают в архив. total_score и tags живут в news_analysis —
# берём их джойном, чтобы архивная строка читалась без обращения к базе.
_SELECT = """
    SELECT n.id, n.published_ts, n.parsed_at, n.source, n.title, n.url,
           n.description, n.plain_text, n.status, a.total_score, a.tags_data
    FROM news n
    LEFT JOIN news_analysis a ON a.news_id = n.id
    WHERE n.archived_at IS NULL
      AND n.plain_text IS NOT NULL AND n.plain_text <> ''
      AND COALESCE(NULLIF(n.published_ts, ''), n.parsed_at) < {ph}
    ORDER BY COALESCE(NULLIF(n.published_ts, ''), n.parsed_at)
    LIMIT {lim}
"""


def _tab_name(parsed_at: str, published_ts: str = "") -> str:
    """Вкладка месяца по дате ПАРСИНГА: Archive-2026-08.

    Не по дате публикации: через sitemap и homepage в базу попадают архивные
    материалы 2013–2025 годов, и разбивка по ней плодила бы сотню вкладок на
    один прогон. Дата парсинга — это время жизни системы, вкладок выходит
    столько, сколько месяцев она работает. Сама дата публикации остаётся
    колонкой внутри вкладки.
    """
    raw = (parsed_at or published_ts or "")[:7]
    if len(raw) == 7 and raw[4] == "-":
        return f"Archive-{raw}"
    return "Archive-unknown"


def _rows_to_dicts(rows: list) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "id": r[0], "published_ts": r[1], "parsed_at": r[2], "source": r[3],
            "title": r[4], "url": r[5], "description": r[6], "plain_text": r[7],
            "status": r[8], "total_score": r[9], "tags": r[10],
        })
    return out


def archive_old_news(max_age_days=None, limit=None) -> dict:
    """Выгружает тексты старше max_age_days в Sheets и чистит их в базе.

    Чистка идёт почанково, сразу после записи чанка: обрыв на середине списка
    не должен стереть текст у строк, которые в Sheets не доехали.
    """
    import config
    if not getattr(config, "ARCHIVE_ENABLED", True):
        return {"archived": 0, "skipped": 0, "errors": 0, "reason": "disabled"}

    from storage.sheets import get_sheets_config_error, write_archive_chunk
    cfg_err = get_sheets_config_error()
    if cfg_err:
        logger.warning("Архив пропущен: %s", cfg_err)
        return {"archived": 0, "skipped": 0, "errors": 0, "reason": cfg_err}

    if max_age_days is None:
        max_age_days = getattr(config, "PLAINTEXT_RETENTION_DAYS", 3)
    if limit is None:
        limit = getattr(config, "ARCHIVE_RUN_LIMIT", 1000)

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=max_age_days)).isoformat()
    ph = "%s" if _is_postgres() else "?"

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(_SELECT.format(ph=ph, lim=int(limit)), (cutoff,))
        items = _rows_to_dicts(cur.fetchall())
    finally:
        cur.close()

    if not items:
        return {"archived": 0, "skipped": 0, "errors": 0}

    # Группируем по вкладке месяца, внутри режем на чанки под лимит Sheets.
    by_tab: dict[str, list[dict]] = {}
    for it in items:
        by_tab.setdefault(_tab_name(it["parsed_at"], it["published_ts"]), []).append(it)

    chunk_size = getattr(config, "SHEETS_BATCH_SIZE", 25)
    archived = skipped = errors = 0
    stamp = now.isoformat()

    for tab, tab_items in by_tab.items():
        for start in range(0, len(tab_items), chunk_size):
            chunk = tab_items[start:start + chunk_size]
            for it in chunk:
                it["archived_at"] = stamp
            try:
                res = write_archive_chunk(chunk, tab)
            except Exception as e:
                logger.error("Архив: чанк %s упал: %s", tab, e)
                errors += len(chunk)
                continue

            done = [i for i in (res.get("written") or []) + (res.get("skipped") or []) if i]
            if not done:
                errors += len(chunk)
                continue

            _purge_texts(done, stamp)
            archived += len(res.get("written") or [])
            skipped += len(res.get("skipped") or [])

    logger.info("Архив: выгружено %d, уже было в Sheets %d, не записано %d",
                archived, skipped, errors)
    return {"archived": archived, "skipped": skipped, "errors": errors}


def _purge_texts(news_ids: list, stamp: str) -> None:
    """Обнуляет plain_text и ставит archived_at у записей, доехавших в Sheets."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        placeholders = ",".join([ph] * len(news_ids))
        cur.execute(
            f"UPDATE news SET plain_text = '', archived_at = {ph} WHERE id IN ({placeholders})",
            (stamp, *news_ids),
        )
        if not _is_postgres():
            conn.commit()
    finally:
        cur.close()
