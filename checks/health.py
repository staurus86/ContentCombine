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
    cutoff_dead = (now - timedelta(days=config.SOURCE_DEAD_DAYS)).isoformat()
    stale_after_min = config.SOURCE_PROBE_STALE_HOURS * 60

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

        # Статус меряет ОДНО: дозвонились ли мы до площадки. Как часто она сама
        # публикует — не наша поломка и живёт в отдельных колонках (count_24h,
        # last_parsed). Раньше healthy требовал публикацию за 3 часа, при том что
        # в тихом режиме цикл обходит источники раз в сутки, — панель показывала
        # ноль здоровых и 132 «down» на полностью исправной системе.
        sh_pre = sh_status.get(name, {})
        ok_min = sh_pre.get("ok_minutes_ago")
        failures = sh_pre.get("consecutive_failures", 0) or 0
        auto_off = sh_pre.get("disabled_at") is not None
        probed_ok_recently = ok_min is not None and ok_min <= stale_after_min

        if name not in conf_by_name:
            # Записи в базе есть, а источника в config.SOURCES уже нет: его убрали,
            # а история осталась. Опрашивать нечего — это не поломка и не «ещё не
            # дошла очередь», иначе такие имена вечно висят в панели как непонятные.
            status = "removed"
        elif name in manually_off:
            status = "off"          # выключен вручную, цикл его не опрашивает
        elif auto_off:
            status = "dead"         # серия сбоев подряд, источник снят с опроса
        elif failures > 0:
            status = "down"         # последний опрос не удался, но ещё пробуем
        elif ok_min is None:
            # Процесс перезапустился и до источника ещё не дошла очередь. Это не
            # поломка — просто нечего сказать.
            status = "unknown"
        elif not probed_ok_recently:
            status = "stale"        # опрос был удачным, но давно: цикл не доходит
        elif last_parsed and last_parsed > cutoff_dead:
            status = "healthy"      # отвечает и публикует
        else:
            status = "silent"       # отвечает, но давно ничего не выпускал

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
            # probe — когда источник опрашивали, ok — когда опрос удался.
            # None — с перезапуска процесса до него ещё не дошла очередь.
            "probe_minutes_ago": sh.get("probe_minutes_ago"),
            "ok_minutes_ago": sh.get("ok_minutes_ago"),
            "manually_disabled": name in manually_off,
        })

    # Сначала то, что требует вмешательства; healthy — в самый низ.
    _ORDER = {"dead": 0, "down": 1, "stale": 2, "off": 3, "unknown": 4,
              "silent": 5, "healthy": 6, "removed": 7}
    results.sort(key=lambda x: (_ORDER.get(x["status"], 9), -x["count_24h"]))

    return results
