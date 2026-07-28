"""Health Monitor — проверка работоспособности источников."""

import logging
from datetime import datetime, timezone, timedelta
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def _source_link(conf: dict) -> str:
    """Кликабельная ссылка на источник по его конфигу (для ручной проверки)."""
    if not conf:
        return ""
    t = conf.get("type")
    if t == "telegram":
        ch = conf.get("channel", "")
        return f"https://t.me/{ch}" if ch else ""
    if t == "bluesky":
        h = conf.get("handle", "")
        return f"https://bsky.app/profile/{h}" if h else ""
    return conf.get("url", "") or ""


def get_sources_health() -> list[dict]:
    """Возвращает статус здоровья каждого источника за последние 24ч."""
    import config

    conn = get_connection()
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_3h = (now - timedelta(hours=3)).isoformat()
    cutoff_dead = (now - timedelta(days=config.SOURCE_DEAD_DAYS)).isoformat()

    ph = "%s" if _is_postgres() else "?"

    try:
        # last_parsed — абсолютная дата последней публикации (по всей истории),
        # count_24h — активность за сутки. "Мёртвость" определяем по времени
        # молчания (cutoff_dead), а не по нулю за 24ч: редкие официальные
        # источники публикуют раз в неделю и не должны висеть как dead.
        cur.execute(f"""
            SELECT source,
                   SUM(CASE WHEN parsed_at > {ph} THEN 1 ELSE 0 END) as count_24h,
                   MAX(parsed_at) as last_parsed
            FROM news
            GROUP BY source
            ORDER BY count_24h DESC
        """, (cutoff_24h,))

        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()

    # Build lookup by source name
    db_sources = {row["source"]: row for row in rows}

    # Live failure info (in-memory: error type / last error / streak) to explain WHY a
    # source is dead — DNS/404/битый фид → удалять, таймаут → чинить.
    sh_status = {}
    try:
        from core.source_health import source_health
        sh_status = source_health.get_status()
    except Exception:
        pass

    # Выключенные из дашборда. Цикл парсинга их пропускает, но в панели это никак
    # не отражалось: 16 источников выключили 23 июня, и полтора месяца они висели
    # как «dead» — при живых площадках и рабочих парсерах.
    try:
        from core.feature_flags import get_disabled_sources
        manually_off = set(get_disabled_sources())
    except Exception:
        manually_off = set()

    # Include ALL configured sources
    conf_by_name = {s["name"]: s for s in config.SOURCES}
    all_source_names = [s["name"] for s in config.SOURCES]
    # Also include sources from DB that aren't in config
    for row in rows:
        if row["source"] not in all_source_names:
            all_source_names.append(row["source"])

    results = []
    for name in all_source_names:
        row = db_sources.get(name)
        if row:
            last_parsed = row["last_parsed"] or ""
            count = row["count_24h"] or 0
        else:
            last_parsed = ""
            count = 0

        # Опрашиваем мы источник или нет — отдельный вопрос от того, публикует ли он.
        # Раньше статус считался только по появлению новых записей, и площадка с
        # публикацией раз в месяц была неотличима от сломанного парсера: панель
        # показывала 104 «dead» при 38 реально молчащих (аудит 2026-07-28).
        sh_pre = sh_status.get(name, {})
        probe_min = sh_pre.get("probe_minutes_ago")
        probed_recently = probe_min is not None and probe_min <= 24 * 60

        if name in manually_off:
            status = "off"          # выключен вручную, цикл его не опрашивает
        elif not last_parsed:
            status = "silent" if probed_recently else "dead"
        elif last_parsed <= cutoff_dead:
            status = "silent" if probed_recently else "dead"
        elif last_parsed > cutoff_3h:
            status = "healthy" if count >= 10 else "low"
        elif last_parsed > cutoff_24h:
            status = "warning"
        else:
            status = "down"          # жив, но публикует редко (1д…порог) — не dead

        # Calculate minutes since last parse
        minutes_ago = -1
        if last_parsed:
            try:
                lp = datetime.fromisoformat(last_parsed.replace("Z", "+00:00"))
                if lp.tzinfo is None:
                    lp = lp.replace(tzinfo=timezone.utc)
                minutes_ago = int((datetime.now(timezone.utc) - lp).total_seconds() / 60)
            except Exception:
                pass

        sh = sh_status.get(name, {})
        results.append({
            "source": name,
            "url": _source_link(conf_by_name.get(name)),
            "count_24h": count,
            "last_parsed": last_parsed,
            "minutes_ago": minutes_ago,
            "status": status,
            "error_type": sh.get("error_type", "") or "",
            "last_error": (sh.get("last_error", "") or "")[:160],
            "consecutive_failures": sh.get("consecutive_failures", 0) or 0,
            "auto_disabled": sh.get("disabled_at") is not None,
            # Когда мы в последний раз ДОЗВОНИЛИСЬ до источника (в минутах назад).
            # None — с перезапуска процесса опроса ещё не было.
            "probe_minutes_ago": sh.get("probe_minutes_ago"),
            "manually_disabled": name in manually_off,
        })

    # Sort: dead first, then by count desc
    _ORDER = {"off": 0, "dead": 1, "silent": 2, "down": 3, "warning": 4, "low": 5, "healthy": 6}
    results.sort(key=lambda x: (_ORDER.get(x["status"], 9), -x["count_24h"]))

    return results
