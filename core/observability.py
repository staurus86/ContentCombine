"""
Observability module: API cost tracking, structured logging helpers, correlation IDs.

API cost tracking records every external API call (LLM, Keys.so, Google Trends)
with estimated cost, latency, and result status.

Usage:
    from core.observability import track_api_call, get_cost_summary
    track_api_call("llm", model="gpt-4o-mini", tokens_in=500, tokens_out=200, cost_usd=0.001)
"""

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Thread-local correlation ID
_local = threading.local()


def set_correlation_id(cid: str = None):
    """Set correlation ID for current thread. Auto-generates if None."""
    _local.correlation_id = cid or uuid.uuid4().hex[:12]
    return _local.correlation_id


def get_correlation_id() -> str:
    """Get current thread's correlation ID."""
    return getattr(_local, "correlation_id", "no-cid")


class CorrelationFilter(logging.Filter):
    """Adds correlation_id to log records."""
    def filter(self, record):
        record.correlation_id = get_correlation_id()
        return True


def setup_structured_logging():
    """Enhance root logger with correlation ID filter."""
    root = logging.getLogger()
    filt = CorrelationFilter()
    for handler in root.handlers:
        handler.addFilter(filt)
    # Update format to include correlation_id
    fmt = "%(asctime)s [%(levelname)s] %(name)s [%(correlation_id)s]: %(message)s"
    formatter = logging.Formatter(fmt)
    for handler in root.handlers:
        handler.setFormatter(formatter)
    logger.info("Structured logging initialized")


def _get_db():
    from storage.database import get_connection, _is_postgres
    return get_connection(), _is_postgres()


def init_observability_tables():
    """Create api_cost_log and decision_trace tables."""
    conn, is_pg = _get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_cost_log (
            id TEXT PRIMARY KEY,
            api_type TEXT NOT NULL,
            endpoint TEXT DEFAULT '',
            model TEXT DEFAULT '',
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            latency_ms INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok',
            news_id TEXT DEFAULT '',
            correlation_id TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS decision_trace (
            id TEXT PRIMARY KEY,
            news_id TEXT NOT NULL,
            step TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT DEFAULT '',
            details TEXT DEFAULT '{}',
            score_before INTEGER DEFAULT 0,
            score_after INTEGER DEFAULT 0,
            correlation_id TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    # Config audit log
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config_audit (
            id TEXT PRIMARY KEY,
            setting_name TEXT NOT NULL,
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            changed_by TEXT DEFAULT 'system',
            created_at TEXT NOT NULL
        )
    """)

    if not is_pg:
        conn.commit()
    cur.close()
    logger.info("Observability tables initialized")


def track_api_call(api_type: str, endpoint: str = "", model: str = "",
                   tokens_in: int = 0, tokens_out: int = 0,
                   cost_usd: float = 0.0, latency_ms: int = 0,
                   status: str = "ok", news_id: str = "",
                   error_message: str = ""):
    """Record an API call to cost log. Non-blocking, best-effort."""
    try:
        from core.feature_flags import is_enabled
        if not is_enabled("api_cost_tracking_v1"):
            return

        conn, is_pg = _get_db()
        cur = conn.cursor()
        ph = "%s" if is_pg else "?"
        now = datetime.now(timezone.utc).isoformat()
        call_id = uuid.uuid4().hex[:16]
        cid = get_correlation_id()

        cur.execute(f"""
            INSERT INTO api_cost_log
            (id, api_type, endpoint, model, tokens_in, tokens_out, cost_usd,
             latency_ms, status, news_id, correlation_id, error_message, created_at)
            VALUES ({','.join([ph]*13)})
        """, (call_id, api_type, endpoint, model, tokens_in, tokens_out, cost_usd,
              latency_ms, status, news_id, cid, error_message[:500], now))
        if not is_pg:
            conn.commit()
        cur.close()
    except Exception as e:
        logger.debug("Failed to track API call: %s", e)


def log_decision(news_id: str, step: str, decision: str, reason: str = "",
                 details: dict = None, score_before: int = 0, score_after: int = 0):
    """Record a pipeline decision for a news item. Non-blocking, best-effort."""
    try:
        from core.feature_flags import is_enabled
        if not is_enabled("decision_trace_v1"):
            return

        conn, is_pg = _get_db()
        cur = conn.cursor()
        ph = "%s" if is_pg else "?"
        now = datetime.now(timezone.utc).isoformat()
        trace_id = uuid.uuid4().hex[:16]
        cid = get_correlation_id()

        cur.execute(f"""
            INSERT INTO decision_trace
            (id, news_id, step, decision, reason, details, score_before, score_after,
             correlation_id, created_at)
            VALUES ({','.join([ph]*10)})
        """, (trace_id, news_id, step, decision, reason,
              json.dumps(details or {}, ensure_ascii=False), score_before, score_after,
              cid, now))
        if not is_pg:
            conn.commit()
        cur.close()
    except Exception as e:
        logger.debug("Failed to log decision: %s", e)


def log_config_change(setting_name: str, old_value: str, new_value: str,
                      changed_by: str = "admin"):
    """Record a config change to audit log."""
    try:
        conn, is_pg = _get_db()
        cur = conn.cursor()
        ph = "%s" if is_pg else "?"
        now = datetime.now(timezone.utc).isoformat()
        change_id = uuid.uuid4().hex[:16]

        cur.execute(f"""
            INSERT INTO config_audit (id, setting_name, old_value, new_value, changed_by, created_at)
            VALUES ({','.join([ph]*6)})
        """, (change_id, setting_name, str(old_value)[:500], str(new_value)[:500], changed_by, now))
        if not is_pg:
            conn.commit()
        cur.close()
    except Exception as e:
        logger.debug("Failed to log config change: %s", e)


def get_cost_summary(days: int = 1) -> dict:
    """Get API cost summary for the last N days."""
    try:
        conn, is_pg = _get_db()
        cur = conn.cursor()
        ph = "%s" if is_pg else "?"
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        cur.execute(f"""
            SELECT api_type,
                   COUNT(*) as call_count,
                   COALESCE(SUM(cost_usd), 0) as total_cost,
                   COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                   COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                   COALESCE(AVG(latency_ms), 0) as avg_latency,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
            FROM api_cost_log
            WHERE created_at >= {ph}
            GROUP BY api_type
        """, (cutoff,))

        if is_pg:
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
        cur.close()

        total_cost = sum(r.get("total_cost", 0) for r in rows)
        total_calls = sum(r.get("call_count", 0) for r in rows)

        return {
            "period_days": days,
            "total_cost_usd": round(total_cost, 4),
            "total_calls": total_calls,
            "by_type": rows,
        }
    except Exception as e:
        logger.debug("Failed to get cost summary: %s", e)
        return {"period_days": days, "total_cost_usd": 0, "total_calls": 0, "by_type": []}


def get_decision_trace(news_id: str) -> list[dict]:
    """Get all decision trace entries for a news item."""
    try:
        conn, is_pg = _get_db()
        cur = conn.cursor()
        ph = "%s" if is_pg else "?"

        cur.execute(f"""
            SELECT * FROM decision_trace
            WHERE news_id = {ph}
            ORDER BY created_at ASC
        """, (news_id,))

        if is_pg:
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
        cur.close()

        # Parse details JSON
        for row in rows:
            if isinstance(row.get("details"), str):
                try:
                    row["details"] = json.loads(row["details"])
                except (ValueError, TypeError):
                    pass
        return rows
    except Exception as e:
        logger.debug("Failed to get decision trace: %s", e)
        return []


def get_config_audit(limit: int = 50) -> list[dict]:
    """Get recent config changes."""
    try:
        conn, is_pg = _get_db()
        cur = conn.cursor()
        ph = "%s" if is_pg else "?"
        cur.execute(f"SELECT * FROM config_audit ORDER BY created_at DESC LIMIT {ph}", (limit,))
        if is_pg:
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
        cur.close()
        return rows
    except Exception as e:
        logger.debug("Failed to get config audit: %s", e)
        return []
