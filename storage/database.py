import hashlib
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import config

logger = logging.getLogger(__name__)

_local = threading.local()  # Per-thread connections (SQLite and PostgreSQL)
_conn_lock = threading.Lock()


def _is_postgres():
    return config.DATABASE_URL.startswith("postgres")


def get_connection():
    if _is_postgres():
        # Per-thread connections for PostgreSQL (thread-safe for ThreadingHTTPServer)
        conn = getattr(_local, 'pg_conn', None)
        if conn is not None:
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return conn
            except Exception:
                logger.warning("PostgreSQL thread connection lost, reconnecting...")
                try:
                    conn.close()
                except Exception:
                    pass
                _local.pg_conn = None

        # Create new connection with retry
        import psycopg2
        url = config.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)

        for attempt in range(3):
            try:
                conn = psycopg2.connect(url, connect_timeout=10)
                conn.autocommit = True
                cur = conn.cursor()
                cur.execute("SET statement_timeout = '120s'")
                cur.close()
                _local.pg_conn = conn
                if attempt > 0:
                    logger.info("PostgreSQL connected (attempt %d)", attempt + 1)
                return conn
            except Exception as e:
                logger.warning("PostgreSQL connect attempt %d/3: %s", attempt + 1, e)
                if attempt < 2:
                    import time as _time; _time.sleep(2 * (attempt + 1))
                else:
                    logger.error("PostgreSQL connection failed after 3 attempts")
                    raise
    else:
        # Per-thread connections for SQLite (avoids "database is locked")
        conn = getattr(_local, 'conn', None)
        if conn is not None:
            return conn
        db_path = config.DATABASE_URL.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
        return conn


@contextmanager
def db_cursor():
    """Context manager for safe cursor lifecycle."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()


def ph():
    """Returns the correct placeholder for the current DB engine."""
    return "%s" if _is_postgres() else "?"


def rows_to_dicts(cur) -> list[dict]:
    """Converts cursor results to list of dicts."""
    if _is_postgres():
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    return [dict(row) for row in cur.fetchall()]


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    try:
        _init_db_impl(conn, cur)
    finally:
        cur.close()
    try:
        _backfill_is_top()
    except Exception as e:
        logger.warning("is_top backfill skipped: %s", e)
    logger.info("Database initialized")


def _backfill_is_top():
    """One-time: flag existing high-score news as top so the ТОП tab covers all
    time, not only items reviewed after the feature shipped. Guarded by a marker."""
    import config
    if get_app_setting("is_top_backfilled") == "1":
        return
    threshold = getattr(config, "TOP_SCORE_THRESHOLD", 70)
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(
            f"UPDATE news SET is_top = 1 WHERE COALESCE(is_top, 0) = 0 "
            f"AND id IN (SELECT news_id FROM news_analysis WHERE total_score >= {ph})",
            (threshold,))
        if not _is_postgres():
            conn.commit()
        logger.info("is_top backfill: flagged existing news with total_score >= %d", threshold)
    finally:
        cur.close()
    set_app_setting("is_top_backfilled", "1")


def get_app_setting(key: str, default: str = "") -> str:
    """Get persistent setting from DB. Returns default if not found."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        _ph = "%s" if _is_postgres() else "?"
        cur.execute(f"SELECT value FROM app_settings WHERE key = {_ph}", (key,))
        row = cur.fetchone()
        if row:
            return row[0] if _is_postgres() else row["value"]
        return default
    except Exception:
        return default
    finally:
        cur.close()


def set_app_setting(key: str, value: str, user: str = "admin"):
    """Persist a setting to DB. Upserts."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        now = datetime.now(timezone.utc).isoformat()
        _ph = "%s" if _is_postgres() else "?"
        if _is_postgres():
            cur.execute(f"""
                INSERT INTO app_settings (key, value, updated_at, updated_by)
                VALUES ({_ph}, {_ph}, {_ph}, {_ph})
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by
            """, (key, value, now, user))
        else:
            cur.execute(f"""
                INSERT OR REPLACE INTO app_settings (key, value, updated_at, updated_by)
                VALUES ({_ph}, {_ph}, {_ph}, {_ph})
            """, (key, value, now, user))
            conn.commit()
    finally:
        cur.close()


def get_all_app_settings() -> dict:
    """Get all persistent settings as a dict."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT key, value FROM app_settings")
        if _is_postgres():
            return {row[0]: row[1] for row in cur.fetchall()}
        return {row["key"]: row["value"] for row in cur.fetchall()}
    except Exception:
        return {}
    finally:
        cur.close()


def _init_db_impl(conn, cur):
    articles_sql = """
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            news_id TEXT,
            title TEXT,
            text TEXT,
            seo_title TEXT,
            seo_description TEXT,
            tags TEXT,
            style TEXT,
            language TEXT DEFAULT 'русский',
            original_title TEXT,
            original_text TEXT,
            source_url TEXT,
            status TEXT DEFAULT 'draft',
            created_at TEXT,
            updated_at TEXT
        )
    """

    task_queue_sql = """
        CREATE TABLE IF NOT EXISTS task_queue (
            id TEXT PRIMARY KEY,
            task_type TEXT,
            news_id TEXT,
            news_title TEXT,
            style TEXT,
            status TEXT DEFAULT 'pending',
            result TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """

    feedback_sql = """
        CREATE TABLE IF NOT EXISTS feedback_stats (
            id TEXT PRIMARY KEY,
            stat_type TEXT,
            stat_key TEXT,
            approved INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            weight_adjustment REAL DEFAULT 0.0,
            updated_at TEXT
        )
    """

    prompt_versions_sql = """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id TEXT PRIMARY KEY,
            prompt_name TEXT,
            version INTEGER,
            content TEXT,
            avg_score REAL DEFAULT 0.0,
            usage_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 0,
            created_at TEXT,
            notes TEXT
        )
    """

    digests_sql = """
        CREATE TABLE IF NOT EXISTS digests (
            id TEXT PRIMARY KEY,
            digest_date TEXT,
            style TEXT,
            title TEXT,
            text TEXT,
            news_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    """

    # Records which news went into each published digest, for cross-day story dedup
    # (the same story is covered by new articles daily; id-based exclusion misses them).
    digest_history_sql = """
        CREATE TABLE IF NOT EXISTS digest_history (
            news_id TEXT,
            title TEXT,
            digest_date TEXT,
            created_at TEXT
        )
    """

    viral_triggers_sql = """
        CREATE TABLE IF NOT EXISTS viral_triggers_config (
            trigger_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            weight INTEGER DEFAULT 0,
            keywords TEXT DEFAULT '[]',
            is_active INTEGER DEFAULT 1,
            is_custom INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """

    news_sql = """
        CREATE TABLE IF NOT EXISTS news (
            id TEXT PRIMARY KEY,
            source TEXT,
            url TEXT,
            title TEXT,
            h1 TEXT,
            description TEXT,
            plain_text TEXT,
            published_at TEXT,
            parsed_at TEXT,
            status TEXT DEFAULT 'new'
        )
    """
    analysis_sql = """
        CREATE TABLE IF NOT EXISTS news_analysis (
            news_id TEXT PRIMARY KEY REFERENCES news(id),
            bigrams TEXT,
            trigrams TEXT,
            trends_data TEXT,
            keyso_data TEXT,
            llm_recommendation TEXT,
            llm_trend_forecast TEXT,
            llm_merged_with TEXT,
            sheets_row INTEGER,
            processed_at TEXT
        )
    """

    if _is_postgres():
        cur.execute(news_sql)
        cur.execute(analysis_sql)
        cur.execute(articles_sql)
        cur.execute(task_queue_sql)
        cur.execute(feedback_sql)
        cur.execute(prompt_versions_sql)
        cur.execute(digests_sql)
        cur.execute(digest_history_sql)
        cur.execute(viral_triggers_sql)
    else:
        cur.execute(news_sql)
        cur.execute(analysis_sql)
        cur.execute(articles_sql)
        cur.execute(task_queue_sql)
        cur.execute(feedback_sql)
        cur.execute(prompt_versions_sql)
        cur.execute(digests_sql)
        cur.execute(digest_history_sql)
        cur.execute(viral_triggers_sql)
        conn.commit()

    # Add check_data columns if missing (stores viral, sentiment, freshness, tags as JSON)
    _add_column_if_missing(cur, "news_analysis", "viral_score", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "viral_level", "TEXT DEFAULT ''")
    _add_column_if_missing(cur, "news_analysis", "viral_data", "TEXT DEFAULT '{}'")
    _add_column_if_missing(cur, "news_analysis", "sentiment_label", "TEXT DEFAULT ''")
    _add_column_if_missing(cur, "news_analysis", "sentiment_score", "REAL DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "freshness_status", "TEXT DEFAULT ''")
    _add_column_if_missing(cur, "news_analysis", "freshness_hours", "REAL DEFAULT -1")
    _add_column_if_missing(cur, "news_analysis", "tags_data", "TEXT DEFAULT '[]'")
    _add_column_if_missing(cur, "news_analysis", "momentum_score", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "headline_score", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "total_score", "INTEGER DEFAULT 0")
    # Этап 2: расширенные check results для единой таблицы
    _add_column_if_missing(cur, "news_analysis", "quality_score", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "relevance_score", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "all_checks_pass", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "entity_names", "TEXT DEFAULT '[]'")
    _add_column_if_missing(cur, "news_analysis", "entity_best_tier", "TEXT DEFAULT ''")
    _add_column_if_missing(cur, "news_analysis", "reviewed_at", "TEXT DEFAULT ''")

    # Articles: scheduled publication time
    _add_column_if_missing(cur, "articles", "scheduled_at", "TEXT")

    # Soft-delete support
    _add_column_if_missing(cur, "news", "is_deleted", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news", "deleted_at", "TEXT")
    # Content metrics: word count (from plain_text) and image count (from article body)
    _add_column_if_missing(cur, "news", "word_count", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news", "image_count", "INTEGER DEFAULT 0")
    # Cases: manual bookmark / auto by research tag. Exempt from freshness purge.
    _add_column_if_missing(cur, "news", "is_case", "INTEGER DEFAULT 0")
    # Top: auto-flagged when total_score >= TOP_SCORE_THRESHOLD. Sticky, exempt from
    # all auto-cleanup so top items accumulate over all time.
    _add_column_if_missing(cur, "news", "is_top", "INTEGER DEFAULT 0")
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_top ON news(is_top)")
    except Exception:
        pass
    # Normalized article date (ISO UTC) — ЕДИНЫЙ источник истины для ВСЕХ фильтров по дате
    # (published_at бывает ISO/RFC822 → нельзя сравнивать в SQL; published_ts всегда ISO).
    _add_column_if_missing(cur, "news", "published_ts", "TEXT")
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_published_ts ON news(published_ts)")
    except Exception:
        pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_case ON news(is_case)")
    except Exception:
        pass
    _add_column_if_missing(cur, "articles", "is_deleted", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "articles", "deleted_at", "TEXT")
    _add_column_if_missing(cur, "articles", "feed_description", "TEXT DEFAULT ''")

    # Phase 0: new columns for explainability (nullable, safe)
    _add_column_if_missing(cur, "news_analysis", "decision_reason", "TEXT DEFAULT ''")
    _add_column_if_missing(cur, "news_analysis", "score_breakdown", "TEXT DEFAULT '{}'")

    # Phase 2: confidence and cluster
    _add_column_if_missing(cur, "news_analysis", "confidence_score", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "news_analysis", "cluster_id", "TEXT DEFAULT ''")

    if not _is_postgres():
        conn.commit()

    # Health log table for uptime metrics
    if _is_postgres():
        cur.execute("""
            CREATE TABLE IF NOT EXISTS health_log (
                id SERIAL PRIMARY KEY,
                timestamp TEXT NOT NULL,
                parsed_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                active_sources INTEGER DEFAULT 0,
                disabled_sources INTEGER DEFAULT 0,
                zombie_threads INTEGER DEFAULT 0,
                active_threads INTEGER DEFAULT 0,
                circuit_breakers_open INTEGER DEFAULT 0,
                scheduler_age_seconds INTEGER DEFAULT 0
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS health_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                parsed_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                active_sources INTEGER DEFAULT 0,
                disabled_sources INTEGER DEFAULT 0,
                zombie_threads INTEGER DEFAULT 0,
                active_threads INTEGER DEFAULT 0,
                circuit_breakers_open INTEGER DEFAULT 0,
                scheduler_age_seconds INTEGER DEFAULT 0
            )
        """)
        conn.commit()

    # Phase 2: article_versions table for content versioning
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_versions (
            id TEXT PRIMARY KEY,
            article_id TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            title TEXT DEFAULT '',
            text TEXT DEFAULT '',
            seo_title TEXT DEFAULT '',
            seo_description TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            change_type TEXT DEFAULT 'manual',
            changed_by TEXT DEFAULT 'system',
            created_at TEXT NOT NULL
        )
    """)
    # Persistent app settings (key-value store)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT,
            updated_by TEXT DEFAULT 'system'
        )
    """)
    if not _is_postgres():
        conn.commit()

    # ─── Indexes for performance ───
    _create_indexes(cur)
    if not _is_postgres():
        conn.commit()

    # Initialize feature flags and observability tables
    try:
        from core.feature_flags import init_flags_table
        init_flags_table()
    except Exception as e:
        logger.warning("Feature flags init skipped: %s", e)
    try:
        from core.observability import init_observability_tables
        init_observability_tables()
    except Exception as e:
        logger.warning("Observability tables init skipped: %s", e)


def _create_indexes(cur):
    """Create indexes for frequently-queried columns (IF NOT EXISTS is safe to re-run)."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_news_status ON news(status)",
        "CREATE INDEX IF NOT EXISTS idx_news_parsed_at ON news(parsed_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_news_source ON news(source)",
        "CREATE INDEX IF NOT EXISTS idx_analysis_score ON news_analysis(total_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_analysis_newsid ON news_analysis(news_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status)",
        "CREATE INDEX IF NOT EXISTS idx_task_queue_type ON task_queue(task_type)",
        "CREATE INDEX IF NOT EXISTS idx_task_queue_created ON task_queue(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)",
        "CREATE INDEX IF NOT EXISTS idx_articles_newsid ON articles(news_id)",
        "CREATE INDEX IF NOT EXISTS idx_news_deleted ON news(is_deleted)",
    ]
    for sql in indexes:
        try:
            cur.execute(sql)
        except Exception as e:
            logger.debug("Index creation skipped: %s", e)


def _add_column_if_missing(cur, table, column, col_type):
    """Безопасно добавляет столбец если его нет."""
    try:
        if _is_postgres():
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass  # Column already exists


_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                    "gclid", "fbclid", "yclid", "ymclid", "_openstat", "mc_cid", "mc_eid",
                    "igshid", "ref_src", "spm"}


def _normalize_url(url: str) -> str:
    """Канонизирует URL для дедупа: убирает #фрагмент, трекинг-параметры (utm_*/gclid…)
    и хвостовой слеш — чтобы один материал с разными ?utm=/#… не плодил дубли."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    try:
        sp = urlsplit((url or "").strip())
        scheme = (sp.scheme or "https").lower()
        netloc = sp.netloc.lower()
        path = sp.path.rstrip("/") or "/"
        q = [(k, v) for k, v in parse_qsl(sp.query) if k.lower() not in _TRACKING_PARAMS]
        return urlunsplit((scheme, netloc, path, urlencode(q), ""))
    except Exception:
        return url or ""


def _news_id(url: str) -> str:
    return hashlib.md5(_normalize_url(url).encode()).hexdigest()


def news_exists(url: str) -> bool:
    news_id = _news_id(url)
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM news WHERE id = %s" if _is_postgres() else "SELECT 1 FROM news WHERE id = ?", (news_id,))
        return cur.fetchone() is not None
    finally:
        cur.close()


# Месяце-карта (первые 3 буквы), EN + RU (именительный/родительный) — без locale-strptime.
_MONTH3 = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def _parse_date_loose(s: str):
    """Парсит дату из множества форматов → aware datetime (UTC) или None.
    ISO / RFC822 / «27 September 2013» / «Jan 26, 2026» / «22 июня 2026» /
    «DD.MM.YYYY» / «MM/DD/YYYY» / «YYYY-MM-DD». Без зависимости от locale."""
    import re
    from email.utils import parsedate_to_datetime
    s = str(s).strip()
    if not s:
        return None
    # 1) ISO 8601 (с Z/мс/offset)
    try:
        iso = s.replace("Z", "+00:00")
        iso = re.sub(r"(\.\d{3})\d+", r"\1", iso)  # обрезать микросекунды >3 знаков
        return datetime.fromisoformat(iso)
    except Exception:
        pass
    # 2) RFC822 (Wed, 17 Jun 2026 ...)
    try:
        d = parsedate_to_datetime(s)
        if d:
            return d
    except Exception:
        pass
    low = s.lower()

    def _mon(tok):
        return _MONTH3.get(tok.strip(".,").lower()[:3])

    # 3) «D Month YYYY» / «D Month, YYYY»
    m = re.search(r"\b(\d{1,2})\s+([A-Za-zА-Яа-яёЁ]{3,})\.?\,?\s+(20\d{2})", s)
    if m and _mon(m.group(2)):
        return datetime(int(m.group(3)), _mon(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
    # 4) «Month D, YYYY» / «Month D YYYY»
    m = re.search(r"\b([A-Za-zА-Яа-яёЁ]{3,})\.?\s+(\d{1,2})\,?\s+(20\d{2})", s)
    if m and _mon(m.group(1)):
        return datetime(int(m.group(3)), _mon(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)
    # 5) DD.MM.YYYY
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b", s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    # 6) YYYY-MM-DD / YYYY/MM/DD
    m = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    # 7) MM/DD/YYYY (US)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def normalize_ts(date_str, fallback_iso: str) -> str:
    """Любую дату (ISO/RFC822/именованные месяцы/числовые форматы/пусто) → ISO UTC.
    Пусто/непарсибельно → fallback_iso. ЕДИНАЯ нормализация для колонки published_ts."""
    if date_str:
        d = _parse_date_loose(date_str)
        if d is not None:
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc).isoformat()
    return fallback_iso


def insert_news(source: str, url: str, title: str, h1: str = "",
                description: str = "", plain_text: str = "", published_at: str = "",
                image_count: int = 0):
    news_id = _news_id(url)
    if news_exists(url):
        return None

    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    word_count = len((plain_text or "").split())
    published_ts = normalize_ts(published_at, now)  # ISO — по этому полю фильтруем даты

    try:
        if _is_postgres():
            cur.execute(
                """INSERT INTO news (id, source, url, title, h1, description, plain_text, published_at, parsed_at, word_count, image_count, published_ts)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (news_id, source, url, title, h1, description, plain_text, published_at, now, word_count, image_count, published_ts)
            )
        else:
            cur.execute(
                """INSERT OR IGNORE INTO news (id, source, url, title, h1, description, plain_text, published_at, parsed_at, word_count, image_count, published_ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (news_id, source, url, title, h1, description, plain_text, published_at, now, word_count, image_count, published_ts)
            )
            conn.commit()
    finally:
        cur.close()

    logger.info("Inserted news: %s — %s", source, title[:60])
    return news_id


def get_unprocessed_news(limit: int = 20):
    conn = get_connection()
    cur = conn.cursor()
    q = "SELECT * FROM news WHERE status = 'approved' ORDER BY parsed_at DESC"
    try:
        if _is_postgres():
            cur.execute(q + " LIMIT %s", (limit,))
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            cur.execute(q + " LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()


def update_news_status(news_id: str, status: str):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if _is_postgres():
            cur.execute("UPDATE news SET status = %s WHERE id = %s", (status, news_id))
        else:
            cur.execute("UPDATE news SET status = ? WHERE id = ?", (status, news_id))
            conn.commit()
    finally:
        cur.close()


def save_analysis(news_id: str, **kwargs):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    import json
    bigrams = json.dumps(kwargs.get("bigrams", []), ensure_ascii=False)
    trigrams = json.dumps(kwargs.get("trigrams", []), ensure_ascii=False)
    trends_data = json.dumps(kwargs.get("trends_data", {}), ensure_ascii=False)
    keyso_data = json.dumps(kwargs.get("keyso_data", {}), ensure_ascii=False)
    llm_recommendation = kwargs.get("llm_recommendation", "")
    llm_trend_forecast = kwargs.get("llm_trend_forecast", "")
    llm_merged_with = json.dumps(kwargs.get("llm_merged_with", []), ensure_ascii=False)
    sheets_row = kwargs.get("sheets_row")

    try:
        if _is_postgres():
            cur.execute(
                """INSERT INTO news_analysis
                   (news_id, bigrams, trigrams, trends_data, keyso_data,
                    llm_recommendation, llm_trend_forecast, llm_merged_with, sheets_row, processed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (news_id) DO UPDATE SET
                    bigrams=EXCLUDED.bigrams, trigrams=EXCLUDED.trigrams,
                    trends_data=EXCLUDED.trends_data, keyso_data=EXCLUDED.keyso_data,
                    llm_recommendation=EXCLUDED.llm_recommendation,
                    llm_trend_forecast=EXCLUDED.llm_trend_forecast,
                    llm_merged_with=EXCLUDED.llm_merged_with,
                    sheets_row=EXCLUDED.sheets_row, processed_at=EXCLUDED.processed_at""",
                (news_id, bigrams, trigrams, trends_data, keyso_data,
                 llm_recommendation, llm_trend_forecast, llm_merged_with, sheets_row, now)
            )
        else:
            # ON CONFLICT DO UPDATE (not INSERT OR REPLACE): only the analysis columns
            # are touched, so total_score / entity_names / viral_* written earlier by
            # save_check_results survive. INSERT OR REPLACE recreated the row and reset
            # every other column to its default — a SQLite-only data wipe (Postgres above
            # already upserts per-column).
            cur.execute(
                """INSERT INTO news_analysis
                   (news_id, bigrams, trigrams, trends_data, keyso_data,
                    llm_recommendation, llm_trend_forecast, llm_merged_with, sheets_row, processed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(news_id) DO UPDATE SET
                    bigrams=excluded.bigrams, trigrams=excluded.trigrams,
                    trends_data=excluded.trends_data, keyso_data=excluded.keyso_data,
                    llm_recommendation=excluded.llm_recommendation,
                    llm_trend_forecast=excluded.llm_trend_forecast,
                    llm_merged_with=excluded.llm_merged_with,
                    sheets_row=excluded.sheets_row, processed_at=excluded.processed_at""",
                (news_id, bigrams, trigrams, trends_data, keyso_data,
                 llm_recommendation, llm_trend_forecast, llm_merged_with, sheets_row, now)
            )
            conn.commit()
    finally:
        cur.close()


def save_check_results(news_id: str, checks: dict, sentiment: dict = None,
                       tags: list = None, momentum: dict = None,
                       headline: dict = None, total_score: int = 0,
                       entities: list = None, score_breakdown: dict = None):
    """Сохраняет результаты проверок (viral, sentiment, freshness и др.) в news_analysis."""
    import json
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"

    viral = checks.get("viral", {})
    freshness = checks.get("freshness", {})
    quality = checks.get("quality", {})
    relevance = checks.get("relevance", {})

    all_pass = all(c.get("pass", False) for c in checks.values())

    # Entity data
    ent_list = entities or []
    ent_names = [e.get("name", "") for e in ent_list[:10]]
    ent_best_tier = ent_list[0].get("tier", "") if ent_list else ""

    vals = {
        "viral_score": viral.get("score", 0),
        "viral_level": viral.get("level", ""),
        "viral_data": json.dumps(viral.get("triggers", []), ensure_ascii=False),
        "sentiment_label": (sentiment or {}).get("label", ""),
        "sentiment_score": (sentiment or {}).get("score", 0),
        "freshness_status": freshness.get("status", ""),
        "freshness_hours": freshness.get("age_hours", -1),
        "tags_data": json.dumps([{"id": t["id"], "label": t["label"]} for t in (tags or [])[:5]], ensure_ascii=False),
        "momentum_score": (momentum or {}).get("score", 0),
        "headline_score": (headline or {}).get("score", 0),
        "total_score": total_score,
        "quality_score": quality.get("score", 0),
        "relevance_score": relevance.get("score", 0),
        "all_checks_pass": 1 if all_pass else 0,
        "entity_names": json.dumps(ent_names, ensure_ascii=False),
        "entity_best_tier": ent_best_tier,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "score_breakdown": json.dumps(score_breakdown or {}, ensure_ascii=False),
    }

    # Ensure row exists in news_analysis
    try:
        if _is_postgres():
            cur.execute(f"INSERT INTO news_analysis (news_id) VALUES ({ph}) ON CONFLICT DO NOTHING", (news_id,))
            set_clause = ", ".join(f"{k} = {ph}" for k in vals)
            cur.execute(f"UPDATE news_analysis SET {set_clause} WHERE news_id = {ph}",
                        list(vals.values()) + [news_id])
        else:
            cur.execute(f"INSERT OR IGNORE INTO news_analysis (news_id) VALUES ({ph})", (news_id,))
            set_clause = ", ".join(f"{k} = {ph}" for k in vals)
            cur.execute(f"UPDATE news_analysis SET {set_clause} WHERE news_id = {ph}",
                        list(vals.values()) + [news_id])
            conn.commit()
    finally:
        cur.close()


def cleanup_old_plaintext(days: int = 14):
    """Очищает plain_text для новостей старше N дней (экономия памяти)."""
    conn = get_connection()
    cur = conn.cursor()
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    _ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"""
            UPDATE news SET plain_text = ''
            WHERE parsed_at < {_ph} AND plain_text != '' AND status IN ('processed', 'ready', 'rejected', 'duplicate')
        """, (cutoff,))
        count = cur.rowcount
        if not _is_postgres():
            conn.commit()
        if count > 0:
            logger.info("Cleaned plain_text for %d old news items", count)
        return count
    finally:
        cur.close()


def cleanup_old_tasks(days: int = 7):
    """Удаляет завершённые/отменённые задачи старше N дней."""
    conn = get_connection()
    cur = conn.cursor()
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"""
            DELETE FROM task_queue
            WHERE created_at < {ph} AND status IN ('done', 'error', 'cancelled', 'skipped')
        """, (cutoff,))
        if _is_postgres():
            count = cur.rowcount
        else:
            count = cur.rowcount
            conn.commit()
        if count > 0:
            logger.info("Cleaned %d old tasks from task_queue", count)
        return count
    finally:
        cur.close()


def save_digest(digest_id: str, digest_date: str, style: str,
                title: str, text: str, news_count: int):
    """Сохраняет дайджест в БД."""
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    ph = "%s" if _is_postgres() else "?"
    try:
        if _is_postgres():
            cur.execute(
                f"""INSERT INTO digests (id, digest_date, style, title, text, news_count, created_at)
                    VALUES ({','.join([ph]*7)})
                    ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, text=EXCLUDED.text,
                    news_count=EXCLUDED.news_count, created_at=EXCLUDED.created_at""",
                (digest_id, digest_date, style, title, text, news_count, now)
            )
        else:
            cur.execute(
                f"""INSERT OR REPLACE INTO digests (id, digest_date, style, title, text, news_count, created_at)
                    VALUES ({','.join([ph]*7)})""",
                (digest_id, digest_date, style, title, text, news_count, now)
            )
            conn.commit()
    finally:
        cur.close()
    logger.info("Saved digest: %s (%s)", title[:60], style)


def record_digest_news(items, digest_date: str = None):
    """Запоминает новости, вошедшие в опубликованный дайджест, для сквозной
    (междневной) дедупликации историй. items: iterable dict'ов с 'id' и 'title'."""
    items = [it for it in (items or []) if (it.get("title") or "").strip()]
    if not items:
        return
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    digest_date = digest_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ph = "%s" if _is_postgres() else "?"
    try:
        for it in items:
            cur.execute(
                f"INSERT INTO digest_history (news_id, title, digest_date, created_at) VALUES ({ph},{ph},{ph},{ph})",
                (it.get("id"), (it.get("title") or "").strip(), digest_date, now)
            )
        if not _is_postgres():
            conn.commit()
    finally:
        cur.close()


def get_recent_digest_titles(days: int = 2) -> list:
    """Заголовки новостей из дайджестов за последние `days` КАЛЕНДАРНЫХ дней,
    ИСКЛЮЧАЯ сегодня — чтобы душить именно повторы «день в день», а не
    переборки одного дня. Источник для сквозной дедупликации."""
    from datetime import timedelta
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        cur.execute(
            f"SELECT title FROM digest_history WHERE digest_date >= {ph} AND digest_date < {ph}",
            (since, today)
        )
        return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        cur.close()


def get_digests(limit: int = 10) -> list[dict]:
    """Возвращает последние дайджесты."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"SELECT * FROM digests ORDER BY created_at DESC LIMIT {ph}", (limit,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()


def delete_digest(digest_id: str) -> bool:
    """Удаляет дайджест по id. Возвращает True, если что-то удалено."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"DELETE FROM digests WHERE id = {ph}", (digest_id,))
        if not _is_postgres():
            conn.commit()
        return cur.rowcount > 0
    finally:
        cur.close()


def log_health_snapshot():
    """Record current health metrics to DB (called every 5 minutes)."""
    import threading as _threading
    from core.watchdog import watchdog
    from core.source_health import source_health
    from core.circuit_breaker import get_circuit_status
    from core.timeouts import get_zombie_thread_count

    try:
        status = source_health.get_status()
        circuits = get_circuit_status()
        health = watchdog.check_health()
        sched = health.get("scheduler", {})

        active_src = sum(1 for s in status.values() if s.get("healthy"))
        disabled_src = sum(1 for s in status.values() if not s.get("healthy"))
        open_circuits = sum(1 for c in circuits.values() if c.get("open"))
        zombies = get_zombie_thread_count()
        active_threads = _threading.active_count()
        sched_age = int(sched.get("age_seconds", 0))

        with db_cursor() as cur:
            cur.execute(
                f"""INSERT INTO health_log
                    (timestamp, parsed_count, failed_count, active_sources, disabled_sources,
                     zombie_threads, active_threads, circuit_breakers_open, scheduler_age_seconds)
                    VALUES ({ph()},{ph()},{ph()},{ph()},{ph()},{ph()},{ph()},{ph()},{ph()})""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    0,  # parsed_count not available per-snapshot
                    disabled_src,
                    active_src,
                    disabled_src,
                    zombies,
                    active_threads,
                    open_circuits,
                    sched_age,
                )
            )
            if not _is_postgres():
                get_connection().commit()
    except Exception as e:
        logger.warning("log_health_snapshot failed: %s", e)
