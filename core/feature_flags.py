"""
Feature Flag System for IgroNews.

Centralized feature flags stored in DB with in-memory cache.
Supports: global enable/disable, per-environment, instant toggle.
All new features must be behind a flag.

Usage:
    from core.feature_flags import is_enabled, set_flag, get_all_flags
    if is_enabled("dashboard_v2"):
        ...
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# In-memory cache with TTL
_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 30  # seconds

# Default flags — all new features start disabled
DEFAULT_FLAGS = {
    "dashboard_v2": {
        "enabled": True,
        "description": "Action-first dashboard tab",
        "phase": 1,
    },
    "explainability_v1": {
        "enabled": True,
        "description": "Score breakdown and decision trace for each news",
        "phase": 1,
    },
    "newsroom_triage_v1": {
        "enabled": True,
        "description": "3-mode triage UX for editorial tab",
        "phase": 2,
    },
    "storyline_mode_v1": {
        "enabled": True,
        "description": "Cluster/storyline grouping of similar news",
        "phase": 3,
    },
    "final_confidence_v1": {
        "enabled": True,
        "description": "Confidence score and decision aids in Final tab",
        "phase": 2,
    },
    "content_versions_v1": {
        "enabled": True,
        "description": "Article version history and diff",
        "phase": 3,
    },
    "seo_extended_v1": {
        "enabled": True,
        "description": "Extended SEO analysis layer",
        "phase": 3,
    },
    "analytics_funnel_v1": {
        "enabled": True,
        "description": "Full funnel analytics and cost visibility",
        "phase": 4,
    },
    "queue_retry_v1": {
        "enabled": True,
        "description": "Enhanced queue observability and retry controls",
        "phase": 2,
    },
    "source_health_plus_v1": {
        "enabled": True,
        "description": "Advanced source health intelligence",
        "phase": 3,
    },
    "admin_safety_v1": {
        "enabled": True,
        "description": "Admin safety features: audit trail, config rollback",
        "phase": 4,
    },
    "api_cost_tracking_v1": {
        "enabled": True,
        "description": "Track API call costs (LLM, Keys.so, Trends)",
        "phase": 0,
    },
    "decision_trace_v1": {
        "enabled": True,
        "description": "Log decision reasons for each news processing step",
        "phase": 0,
    },
}


def _get_db():
    """Lazy import to avoid circular deps."""
    from storage.database import get_connection, _is_postgres
    return get_connection(), _is_postgres()


def init_flags_table():
    """Create feature_flags table and seed defaults. Called from init_db()."""
    conn, is_pg = _get_db()
    cur = conn.cursor()
    ph = "%s" if is_pg else "?"

    cur.execute("""
        CREATE TABLE IF NOT EXISTS feature_flags (
            flag_id TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            phase INTEGER DEFAULT 0,
            updated_at TEXT,
            updated_by TEXT DEFAULT 'system'
        )
    """)
    if not is_pg:
        conn.commit()

    # Seed missing flags (additive only — never overwrite existing)
    now = datetime.now(timezone.utc).isoformat()
    for flag_id, meta in DEFAULT_FLAGS.items():
        try:
            if is_pg:
                cur.execute(f"""
                    INSERT INTO feature_flags (flag_id, enabled, description, phase, updated_at)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
                    ON CONFLICT (flag_id) DO NOTHING
                """, (flag_id, 1 if meta["enabled"] else 0, meta["description"], meta["phase"], now))
            else:
                cur.execute(f"""
                    INSERT OR IGNORE INTO feature_flags (flag_id, enabled, description, phase, updated_at)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
                """, (flag_id, 1 if meta["enabled"] else 0, meta["description"], meta["phase"], now))
        except Exception:
            pass

    if not is_pg:
        conn.commit()

    # Migrate: enable flags that should be on by default but were seeded as disabled
    _ENABLE_BY_DEFAULT = [
        "dashboard_v2", "explainability_v1", "newsroom_triage_v1",
        "final_confidence_v1", "content_versions_v1", "analytics_funnel_v1",
        "queue_retry_v1", "admin_safety_v1",
        "storyline_mode_v1", "source_health_plus_v1", "seo_extended_v1",
    ]
    for flag_id in _ENABLE_BY_DEFAULT:
        cur2 = conn.cursor()
        try:
            # Only enable if still set by 'system' (not manually toggled by admin)
            cur2.execute(
                f"UPDATE feature_flags SET enabled = 1 WHERE flag_id = {ph} AND enabled = 0 AND updated_by = {ph}",
                (flag_id, "system")
            )
            if not is_pg:
                conn.commit()
        except Exception:
            pass
        finally:
            cur2.close()

    cur.close()
    logger.info("Feature flags table initialized (%d flags)", len(DEFAULT_FLAGS))


def _load_from_db():
    """Load all flags from DB into cache."""
    try:
        conn, is_pg = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT flag_id, enabled, description, phase, updated_at, updated_by FROM feature_flags")
        if is_pg:
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
        cur.close()

        result = {}
        for row in rows:
            result[row["flag_id"]] = {
                "enabled": bool(row["enabled"]),
                "description": row.get("description", ""),
                "phase": row.get("phase", 0),
                "updated_at": row.get("updated_at", ""),
                "updated_by": row.get("updated_by", "system"),
            }
        return result
    except Exception as e:
        logger.warning("Failed to load feature flags from DB: %s", e)
        # Fallback to defaults
        return {k: {"enabled": v["enabled"], "description": v["description"],
                     "phase": v["phase"], "updated_at": "", "updated_by": "system"}
                for k, v in DEFAULT_FLAGS.items()}


def _get_cached_flags():
    """Get flags with TTL cache."""
    global _cache
    with _cache_lock:
        now = time.time()
        if _cache and _cache.get("_ts", 0) + _CACHE_TTL > now:
            return _cache
        flags = _load_from_db()
        flags["_ts"] = now
        _cache = flags
        return _cache


def invalidate_cache():
    """Force reload on next access."""
    global _cache
    with _cache_lock:
        _cache = {}


def is_enabled(flag_id: str) -> bool:
    """Check if a feature flag is enabled. Returns False for unknown flags."""
    flags = _get_cached_flags()
    entry = flags.get(flag_id)
    if entry is None:
        return False
    return entry.get("enabled", False)


def set_flag(flag_id: str, enabled: bool, updated_by: str = "admin"):
    """Toggle a feature flag. Persists to DB immediately."""
    conn, is_pg = _get_db()
    cur = conn.cursor()
    ph = "%s" if is_pg else "?"
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur.execute(
            f"UPDATE feature_flags SET enabled = {ph}, updated_at = {ph}, updated_by = {ph} WHERE flag_id = {ph}",
            (1 if enabled else 0, now, updated_by, flag_id)
        )
        if not is_pg:
            conn.commit()
    finally:
        cur.close()
    invalidate_cache()
    logger.info("Feature flag '%s' set to %s by %s", flag_id, enabled, updated_by)


def get_flag(flag_id: str):
    """Get raw flag value. Returns the enabled bool for known flags, or None."""
    flags = _get_cached_flags()
    entry = flags.get(flag_id)
    if entry is None:
        return None
    return entry.get("enabled")


def get_disabled_sources() -> list[str]:
    """Get list of source names disabled via dashboard."""
    conn, is_pg = _get_db()
    cur = conn.cursor()
    ph = "%s" if is_pg else "?"
    try:
        cur.execute(f"SELECT enabled FROM feature_flags WHERE flag_id = {ph}", ("disabled_sources",))
        row = cur.fetchone()
    finally:
        cur.close()
    if row is None:
        return []
    raw = row[0] if not hasattr(row, "keys") else row["enabled"]
    # We store the JSON list in the `description` column to avoid type conflicts
    # Re-read using description column
    conn2, is_pg2 = _get_db()
    cur2 = conn2.cursor()
    ph2 = "%s" if is_pg2 else "?"
    try:
        cur2.execute(f"SELECT description FROM feature_flags WHERE flag_id = {ph2}", ("disabled_sources",))
        row2 = cur2.fetchone()
    finally:
        cur2.close()
    if row2 is None:
        return []
    raw2 = row2[0] if not hasattr(row2, "keys") else row2["description"]
    if not raw2:
        return []
    try:
        result = json.loads(raw2)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _set_disabled_sources(disabled: list):
    """Persist disabled sources list to DB (stored in description column of a special flag)."""
    conn, is_pg = _get_db()
    cur = conn.cursor()
    ph = "%s" if is_pg else "?"
    now = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(disabled)
    try:
        if is_pg:
            cur.execute(f"""
                INSERT INTO feature_flags (flag_id, enabled, description, phase, updated_at, updated_by)
                VALUES ({ph}, 1, {ph}, 0, {ph}, {ph})
                ON CONFLICT (flag_id) DO UPDATE SET description = EXCLUDED.description, updated_at = EXCLUDED.updated_at
            """, ("disabled_sources", encoded, now, "admin"))
        else:
            cur.execute(f"""
                INSERT OR REPLACE INTO feature_flags (flag_id, enabled, description, phase, updated_at, updated_by)
                VALUES ({ph}, 1, {ph}, 0, {ph}, {ph})
            """, ("disabled_sources", encoded, now, "admin"))
        if not is_pg:
            conn.commit()
    finally:
        cur.close()
    invalidate_cache()


def toggle_source(source_name: str, enabled: bool):
    """Enable or disable a source via dashboard."""
    disabled = get_disabled_sources()
    if enabled and source_name in disabled:
        disabled.remove(source_name)
    elif not enabled and source_name not in disabled:
        disabled.append(source_name)
    _set_disabled_sources(disabled)
    logger.info("Source '%s' %s via dashboard", source_name, "enabled" if enabled else "disabled")


def get_all_flags() -> list[dict]:
    """Return all flags as list of dicts for API/UI."""
    flags = _get_cached_flags()
    result = []
    for flag_id, meta in sorted(flags.items()):
        if flag_id == "_ts":
            continue
        result.append({
            "flag_id": flag_id,
            "enabled": meta.get("enabled", False),
            "description": meta.get("description", ""),
            "phase": meta.get("phase", 0),
            "updated_at": meta.get("updated_at", ""),
            "updated_by": meta.get("updated_by", "system"),
        })
    return result
