import gzip
import json
import hmac
import os
import hashlib
import secrets
import threading
import logging
import time as _time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    request_queue_size = 20
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

from storage.database import get_connection, _is_postgres, get_unprocessed_news, update_news_status, init_db

logger = logging.getLogger(__name__)
PORT = int(os.getenv("PORT", 8080))

# Pre-compressed dashboard HTML (computed once at module load, see bottom)
_DASHBOARD_HTML_GZIP = None
_DASHBOARD_HTML_BYTES = None

# Secret key for signing cookies — stable across redeploys via env var
_COOKIE_SECRET = os.getenv("COOKIE_SECRET", "")
if not _COOKIE_SECRET:
    import secrets as _secrets
    _COOKIE_SECRET = _secrets.token_hex(32)
    logging.warning("COOKIE_SECRET not set — generated random key (sessions won't survive restart)")

# Users: {username: {"hash": password_hash, "role": "admin"|"editor"|"viewer"}}
_admin_pw = os.getenv("ADMIN_PASSWORD", "admin123")
USERS = {
    "admin": {"hash": hashlib.sha256(_admin_pw.encode()).hexdigest(), "role": "admin"},
}
_users_lock = __import__("threading").Lock()

# Role permissions
ROLE_PERMISSIONS = {
    "admin": {"read", "write", "approve", "settings", "users", "flags", "delete", "pipeline"},
    "editor": {"read", "write", "approve", "pipeline"},
    "viewer": {"read"},
}

def _user_role(username: str) -> str:
    entry = USERS.get(username, {})
    if isinstance(entry, dict):
        return entry.get("role", "editor")
    return "editor"  # legacy compat

def _user_has_perm(username: str, perm: str) -> bool:
    role = _user_role(username)
    return perm in ROLE_PERMISSIONS.get(role, set())


def _sign_cookie(username: str) -> str:
    """Создаёт подписанную куку: username.expiry.signature"""
    expiry = int(_time.time()) + 86400  # 24 часа
    payload = f"{username}:{expiry}"
    sig = hmac.new(_COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_cookie(value: str) -> str | None:
    """Проверяет подписанную куку. Возвращает username или None."""
    try:
        parts = value.split(":")
        if len(parts) != 3:
            return None
        username, expiry_str, sig = parts
        expiry = int(expiry_str)
        if _time.time() > expiry:
            return None
        payload = f"{username}:{expiry_str}"
        expected = hmac.new(_COOKIE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if username not in USERS:
            return None
        return username
    except Exception:
        return None


class AdminHandler(BaseHTTPRequestHandler):

    def _get_session_user(self):
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie(cookie_header)
        token = cookie.get("session")
        if token:
            return _verify_cookie(token.value)
        return None

    def _require_auth(self):
        """Returns True if authorized, False if redirected to login."""
        if self._get_session_user():
            return True
        path = urlparse(self.path).path
        if path == "/login":
            return True
        if path.startswith("/api/"):
            self._json({"error": "unauthorized"}, 401)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
        return False

    def do_GET(self):
        try:
            from core.watchdog import watchdog
            watchdog.heartbeat("web")
        except Exception:
            pass
        path = urlparse(self.path).path
        # Static files that don't require auth
        if path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *\nDisallow: /\n")
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/login":
            self._serve_login()
            return
        if path == "/logout":
            self._do_logout()
            return
        if path == "/api/diag":
            self._serve_diag()
            return
        if path == "/api/health/sources":
            self._handle_source_health()
            return
        if path == "/api/health/log":
            self._handle_health_log()
            return
        if path == "/api/health/ready":
            self._handle_readiness()
            return
        if not self._require_auth():
            return

        routes = {
            "/": self._serve_dashboard,
            "/api/stats": lambda: self._json(self._get_stats()),
            "/api/pipeline/status": lambda: self._json(self._get_pipeline_status()),
            "/api/news": lambda: self._json(self._get_news()),
            "/api/news/list": lambda: self._json(self._get_news_unified()),
            "/api/news/trash": lambda: self._json(self._get_trash()),
            "/api/sources": lambda: self._json(self._get_sources()),
            "/api/prompts": lambda: self._json(self._get_prompts()),
            "/api/settings": lambda: self._json(self._get_settings()),
            "/api/users": lambda: self._json(self._get_users()),
            "/api/health": lambda: self._json(self._get_health()),
            "/api/dashboard_groups": lambda: self._dashboard_groups(),
            "/api/sources_stats": lambda: self._json(self._get_sources_stats()),
            "/api/db_info": lambda: self._json(self._get_db_info()),
            "/api/articles": lambda: self._json(self._get_articles()),
            "/api/queue": lambda: self._json(self._get_queue()),
            "/api/analytics": lambda: self._json(self._get_analytics()),
            "/api/prompt_versions": lambda: self._json(self._get_prompt_versions()),
            "/api/viral": lambda: self._json(self._get_viral()),
            "/api/logs": lambda: self._json(self._get_logs()),
            "/api/rate_stats": lambda: self._json(self._get_rate_stats()),
            "/api/cache_stats": lambda: self._json(self._get_cache_stats()),
            "/api/editorial": lambda: self._json(self._get_editorial()),
            "/api/moderation_list": lambda: self._json(self._get_moderation_list()),
            "/api/digests": lambda: self._json(self._get_digests()),
            "/api/viral_triggers": lambda: self._json(self._get_viral_triggers()),
            "/api/final": lambda: self._json(self._get_final()),
            # Phase 0: feature flags, observability, dashboard v2
            "/api/feature_flags": lambda: self._json(self._get_feature_flags()),
            "/api/cost_summary": lambda: self._json(self._get_cost_summary()),
            "/api/config_audit": lambda: self._json(self._get_config_audit()),
            "/api/ops_dashboard": lambda: self._json(self._get_ops_dashboard()),
            # Phase 3: analytics funnel, source intelligence
            "/api/analytics/funnel": lambda: self._json(self._get_funnel_analytics()),
            "/api/analytics/cost_by_source": lambda: self._json(self._get_cost_by_source()),
            "/api/analytics/prompt_insights": lambda: self._json(self._get_prompt_insights()),
            # Phase 4: storylines, source health plus, threshold simulator
            "/api/storylines": lambda: self._json(self._get_storylines()),
            "/api/source_health_plus": lambda: self._json(self._get_source_health_plus()),
            "/api/storylines/settings": lambda: self._json(self._get_storylines_settings()),
            "/api/storylines/history": lambda: self._json(self._get_storylines_history()),
        }

        # CMS-ready XLSX export of articles
        if path == "/api/articles/xlsx":
            self._serve_articles_xlsx()
            return

        # DOCX download (GET with query param)
        if path == "/api/articles/docx":
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            aid = qs.get("id", [""])[0]
            if aid:
                self._serve_docx(aid)
            else:
                self._json({"error": "id required"}, 400)
            return

        # XLSX export of filtered news
        if path == "/api/news/xlsx":
            self._serve_news_xlsx()
            return

        # Bulk DOCX (ZIP with multiple DOCX)
        if path == "/api/articles/docx_bulk":
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            ids = qs.get("ids", [""])[0]
            if ids:
                self._serve_docx_bulk(ids.split(","))
            else:
                self._json({"error": "ids required"}, 400)
            return

        # Event chain (temporal clusters)
        if path == "/api/event_chain":
            qs = parse_qs(urlparse(self.path).query)
            news_id = qs.get("news_id", [""])[0]
            if news_id:
                self._json(self._get_event_chain_by_id(news_id))
            else:
                self._json({"error": "news_id required"}, 400)
            return

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            from core.watchdog import watchdog
            watchdog.heartbeat("web")
        except Exception:
            pass
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/login":
            self._do_login(body)
            return
        if not self._require_auth():
            return

        routes = {
            "/api/process": self._run_process,
            "/api/process_one": lambda: self._process_one(body),
            "/api/export_sheets": lambda: self._export_sheets(body),
            "/api/sources/add": lambda: self._add_source(body),
            "/api/sources/edit": lambda: self._edit_source(body),
            "/api/sources/delete": lambda: self._delete_source(body),
            "/api/sources/toggle": lambda: self._handle_toggle_source(body),
            "/api/prompts/save": lambda: self._save_prompts(body),
            "/api/settings/save": lambda: self._save_settings(body),
            "/api/test_llm": lambda: self._test_llm(body),
            "/api/test_keyso": lambda: self._test_keyso(body),
            "/api/reparse": lambda: self._reparse_source(body),
            "/api/test_sheets": lambda: self._test_sheets(body),
            "/api/quick_tags": lambda: self._quick_tags(body),
            "/api/review": lambda: self._run_review(body),
            "/api/review_batch": lambda: self._review_batch(body),
            "/api/approve": lambda: self._approve_news(body),
            "/api/reject": lambda: self._reject_news(body),
            "/api/users/add": lambda: self._add_user(body),
            "/api/users/delete": lambda: self._delete_user(body),
            "/api/users/change_password": lambda: self._change_password(body),
            "/api/news/bulk_status": lambda: self._bulk_status(body),
            "/api/news/delete": lambda: self._delete_news(body),
            "/api/news/restore": lambda: self._restore_news(body),
            "/api/news/purge": lambda: self._purge_news(body),
            "/api/articles/restore": lambda: self._restore_article(body),
            "/api/test_parse": lambda: self._test_parse(body),
            "/api/setup_headers": lambda: self._setup_headers(body),
            "/api/reparse_all": lambda: self._reparse_all(body),
            "/api/rewrite": lambda: self._rewrite_news(body),
            "/api/merge": lambda: self._merge_news(body),
            "/api/news/detail": lambda: self._news_detail(body),
            "/api/export_sheets_bulk": lambda: self._export_sheets_bulk(body),
            "/api/analyze_news": lambda: self._analyze_news(body),
            "/api/batch_rewrite": lambda: self._batch_rewrite(body),
            "/api/articles/save": lambda: self._save_article(body),
            "/api/articles/update": lambda: self._update_article(body),
            "/api/articles/delete": lambda: self._delete_article(body),
            "/api/articles/rewrite": lambda: self._rewrite_article(body),
            "/api/articles/improve": lambda: self._improve_article(body),
            "/api/articles/detail": lambda: self._article_detail(body),
            "/api/articles/schedule": lambda: self._schedule_article(body),
            "/api/preview_rewrite": lambda: self._preview_rewrite(body),
            "/api/prompt_versions/save": lambda: self._save_prompt_version(body),
            "/api/prompt_versions/activate": lambda: self._activate_prompt_version(body),
            "/api/generate_digest": lambda: self._generate_digest(body),
            "/api/digest/generate": lambda: self._generate_and_save_digest(body),
            "/api/event_chain": lambda: self._get_event_chain(body),
            "/api/queue/cancel": lambda: self._cancel_queue_task(body),
            "/api/queue/cancel_all": lambda: self._cancel_all_queue(body),
            "/api/queue/clear_done": lambda: self._clear_done_queue(body),
            "/api/queue/retry": lambda: self._retry_queue_tasks(body),
            "/api/viral_triggers/save": lambda: self._save_viral_trigger(body),
            "/api/viral_triggers/delete": lambda: self._delete_viral_trigger(body),
            "/api/run_auto_review": lambda: self._run_auto_review(body),
            "/api/queue/rewrite": lambda: self._queue_batch_rewrite(body),
            "/api/queue/sheets": lambda: self._queue_sheets_export(body),
            "/api/translate_title": lambda: self._translate_title(body),
            "/api/ai_recommend": lambda: self._ai_recommend(body),
            "/api/cache/clear": lambda: self._clear_cache(body),
            "/api/export_all_processed": lambda: self._export_all_processed(body),
            "/api/export_ready_all": lambda: self._export_ready_all(body),
            "/api/pipeline/full_auto": lambda: self._pipeline_full_auto(body),
            "/api/pipeline/no_llm": lambda: self._pipeline_no_llm(body),
            "/api/pipeline/stop": lambda: self._pipeline_stop(body),
            "/api/moderation": lambda: self._get_moderation(body),
            "/api/moderation/rewrite": lambda: self._moderation_rewrite(body),
            "/api/seo_check": lambda: self._seo_check(body),
            # Phase 0: feature flags, decision trace
            "/api/feature_flags/toggle": lambda: self._toggle_feature_flag(body),
            "/api/decision_trace": lambda: self._get_decision_trace(body),
            # Phase 2: content versioning, multi-output
            "/api/articles/versions": lambda: self._get_article_versions(body),
            "/api/articles/multi_output": lambda: self._generate_multi_output(body),
            "/api/articles/regenerate_field": lambda: self._regenerate_field(body),
            # Phase 4: threshold simulator, rescore
            "/api/simulate_thresholds": lambda: self._simulate_thresholds(body),
            "/api/rescore": lambda: self._rescore_news(body),
            "/api/health/heal": lambda: self._heal_source(body),
            "/api/storylines/export": lambda: self._export_storylines(body),
            "/api/storylines/settings": lambda: self._save_storylines_settings(body),
            "/api/cleanup_short": lambda: self._cleanup_short(body),
            "/api/cleanup_old": lambda: self._cleanup_old(body),
            "/api/trash/export_sheets": lambda: self._export_trash_sheets(body),
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._json({"error": "not found"}, 404)

    # --- Helpers ---
    _MAX_BODY_SIZE = 5 * 1024 * 1024  # 5 MB

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > self._MAX_BODY_SIZE:
            logger.warning("Request body too large: %d bytes", length)
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _accepts_gzip(self):
        return "gzip" in self.headers.get("Accept-Encoding", "")

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin", "")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-cache, no-store")
        # Gzip if body > 1KB and client supports it
        if len(body) > 1024 and self._accepts_gzip():
            body = gzip.compress(body, compresslevel=6)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- Auth ---
    def _do_login(self, body):
        username = body.get("username", "")
        password = body.get("password", "")
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        entry = USERS.get(username, {})
        stored_hash = entry.get("hash", entry) if isinstance(entry, dict) else entry
        if stored_hash == pw_hash:
            signed = _sign_cookie(username)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"session={signed}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=86400")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "role": _user_role(username)}).encode())
        else:
            self._json({"status": "error", "message": "Invalid credentials"}, 401)

    def _do_logout(self):
        self.send_response(302)
        self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
        self.send_header("Location", "/login")
        self.end_headers()

    def _get_users(self):
        return [{"username": u, "role": _user_role(u)} for u in USERS.keys()]

    def _add_user(self, body):
        username = body.get("username", "")
        password = body.get("password", "")
        role = body.get("role", "editor")
        if role not in ROLE_PERMISSIONS:
            role = "editor"
        if not username or not password:
            self._json({"status": "error", "message": "Username and password required"})
            return
        user = self._get_session_user()
        if not _user_has_perm(user, "users"):
            self._json({"status": "error", "message": "Недостаточно прав"}, 403)
            return
        with _users_lock:
            USERS[username] = {"hash": hashlib.sha256(password.encode()).hexdigest(), "role": role}
        self._json({"status": "ok", "users": self._get_users()})

    def _delete_user(self, body):
        username = body.get("username", "")
        if username == "admin":
            self._json({"status": "error", "message": "Cannot delete admin"})
            return
        user = self._get_session_user()
        if not _user_has_perm(user, "users"):
            self._json({"status": "error", "message": "Недостаточно прав"}, 403)
            return
        with _users_lock:
            USERS.pop(username, None)
        self._json({"status": "ok", "users": self._get_users()})

    def _require_perm(self, perm: str) -> bool:
        """Check permission. Returns True if allowed, sends 403 and returns False if denied."""
        user = self._get_session_user()
        if not user:
            self._json({"error": "unauthorized"}, 401)
            return False
        if not _user_has_perm(user, perm):
            self._json({"error": "Недостаточно прав"}, 403)
            return False
        return True

    def _handle_source_health(self):
        """Публичный endpoint: статус источников, circuit breakers, watchdog."""
        from core.source_health import source_health
        from core.circuit_breaker import get_circuit_status
        from core.watchdog import watchdog
        self._json({
            "status": "ok" if watchdog.is_alive() else "degraded",
            "sources": source_health.get_status(),
            "circuits": get_circuit_status(),
            "system": watchdog.get_system_health(),
        })

    def _handle_health_log(self):
        """Публичный endpoint: последние 100 снапшотов health_log."""
        from storage.database import db_cursor, rows_to_dicts
        with db_cursor() as cur:
            cur.execute("SELECT * FROM health_log ORDER BY timestamp DESC LIMIT 100")
            rows = rows_to_dicts(cur)
        self._json({"log": rows})

    def _handle_readiness(self):
        """Readiness probe: 200 only when scheduler is alive."""
        from core.watchdog import watchdog
        if watchdog.is_alive():
            self._json({"ready": True}, 200)
        else:
            self._json({"ready": False, "reason": "scheduler not yet started or stale"}, 503)

    def _serve_diag(self):
        """Публичный диагностический endpoint — проверка БД и парсинга."""
        import json as _json
        import config
        diag = {}
        try:
            # DB connection
            db_url = config.DATABASE_URL
            diag["db_type"] = "PostgreSQL" if db_url.startswith("postgres") else "SQLite"
            diag["db_url_set"] = bool(db_url and db_url != "sqlite:///news.db")

            conn = get_connection()
            cur = conn.cursor()
            try:
                diag["db_connected"] = True

                # Counts
                cur.execute("SELECT COUNT(*) FROM news")
                diag["total_news"] = cur.fetchone()[0]

                cur.execute("SELECT status, COUNT(*) FROM news GROUP BY status")
                status_counts = {}
                for row in cur.fetchall():
                    if _is_postgres():
                        status_counts[row[0]] = row[1]
                    else:
                        status_counts[row[0]] = row[1]
                diag["by_status"] = status_counts

                cur.execute("SELECT COUNT(*) FROM news_analysis")
                diag["total_analyzed"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM news_analysis WHERE reviewed_at IS NOT NULL AND reviewed_at != ''")
                diag["total_reviewed"] = cur.fetchone()[0]

                # Last parsed
                cur.execute("SELECT MAX(parsed_at) FROM news")
                row = cur.fetchone()
                diag["last_parsed"] = str(row[0]) if row and row[0] else "never"

                # Sources parsed
                cur.execute("SELECT source, COUNT(*) FROM news GROUP BY source ORDER BY COUNT(*) DESC")
                src_counts = {}
                for row in cur.fetchall():
                    if _is_postgres():
                        src_counts[row[0]] = row[1]
                    else:
                        src_counts[row[0]] = row[1]
                diag["sources"] = src_counts

                diag["configured_sources"] = len(config.SOURCES)

                # Scoring diagnostics: check recent news for missing data
                ph = "%s" if _is_postgres() else "?"
                cur.execute(f"""
                    SELECT n.id, n.title, n.source, n.status,
                           LENGTH(n.plain_text) as text_len,
                           COALESCE(a.total_score, -1) as score
                    FROM news n
                    LEFT JOIN news_analysis a ON n.id = a.news_id
                    ORDER BY n.parsed_at DESC
                    LIMIT 10
                """)
                if _is_postgres():
                    cols = [d[0] for d in cur.description]
                    recent = [dict(zip(cols, r)) for r in cur.fetchall()]
                else:
                    recent = [dict(r) for r in cur.fetchall()]
                diag["recent_news"] = [{
                    "title": r["title"][:60],
                    "source": r["source"],
                    "status": r["status"],
                    "text_len": r["text_len"] or 0,
                    "score": r["score"],
                } for r in recent]

                # Count news with no analysis at all
                cur.execute("SELECT COUNT(*) FROM news n LEFT JOIN news_analysis a ON n.id = a.news_id WHERE a.news_id IS NULL")
                diag["no_analysis"] = cur.fetchone()[0]

                # Count with score = 0
                cur.execute("SELECT COUNT(*) FROM news_analysis WHERE total_score = 0")
                diag["score_zero"] = cur.fetchone()[0]

                # Source-level audit: text_len=0 and score=0 per source
                cur.execute("""
                    SELECT n.source,
                           COUNT(*) as total,
                           SUM(CASE WHEN LENGTH(COALESCE(n.plain_text, '')) = 0 THEN 1 ELSE 0 END) as no_text,
                           SUM(CASE WHEN LENGTH(COALESCE(n.description, '')) = 0 THEN 1 ELSE 0 END) as no_desc,
                           SUM(CASE WHEN COALESCE(a.total_score, 0) = 0 THEN 1 ELSE 0 END) as score_zero
                    FROM news n
                    LEFT JOIN news_analysis a ON n.id = a.news_id
                    GROUP BY n.source
                    ORDER BY n.source
                """)
                if _is_postgres():
                    cols = [d[0] for d in cur.description]
                    audit_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                else:
                    audit_rows = [dict(r) for r in cur.fetchall()]
                diag["source_audit"] = audit_rows

            finally:
                cur.close()
        except Exception as e:
            diag["db_connected"] = False
            diag["error"] = str(e)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_json.dumps(diag, indent=2, ensure_ascii=False).encode())

    def _serve_login(self):
        html = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IgroNews Login</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%23192734'/><text x='50' y='38' text-anchor='middle' font-size='28' font-family='sans-serif' font-weight='bold' fill='%231da1f2'>IGR</text><text x='50' y='70' text-anchor='middle' font-size='20' font-family='sans-serif' fill='%2317bf63'>NEWS</text><circle cx='82' cy='20' r='8' fill='%23e0245e'/></svg>">
<style>
* { margin:0;padding:0;box-sizing:border-box; }
body { font-family:-apple-system,sans-serif; background:#0f1923; color:#e1e8ed; display:flex; justify-content:center; align-items:center; height:100vh; }
.login { background:#192734; padding:40px; border-radius:12px; width:340px; }
.login h2 { color:#1da1f2; margin-bottom:20px; text-align:center; }
.login input { width:100%; padding:10px 14px; margin-bottom:12px; background:#22303c; border:1px solid #38444d; color:#e1e8ed; border-radius:6px; font-size:0.95em; }
.login input:focus { outline:none; border-color:#1da1f2; }
.login button { width:100%; padding:10px; background:#1da1f2; color:#fff; border:none; border-radius:6px; font-size:1em; cursor:pointer; }
.login button:hover { background:#1a91da; }
.error { color:#e0245e; font-size:0.85em; margin-bottom:10px; display:none; }
</style></head><body>
<div class="login">
  <div style="text-align:center;margin-bottom:10px">
    <svg width="48" height="48" viewBox="0 0 100 100">
      <rect width="100" height="100" rx="20" fill="#1da1f2"/>
      <text x="50" y="40" text-anchor="middle" font-size="30" font-family="sans-serif" font-weight="bold" fill="#fff">IGR</text>
      <text x="50" y="72" text-anchor="middle" font-size="22" font-family="sans-serif" fill="#fff" opacity="0.8">NEWS</text>
      <circle cx="85" cy="18" r="9" fill="#e0245e"/>
    </svg>
  </div>
  <h2>IgroNews</h2>
  <div class="error" id="err">Invalid credentials</div>
  <input id="username" placeholder="Username" autofocus>
  <input id="password" type="password" placeholder="Password">
  <button onclick="login()">Login</button>
</div>
<script>
document.getElementById('password').addEventListener('keypress', e => { if(e.key==='Enter') login(); });
async function login() {
  const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username: document.getElementById('username').value, password: document.getElementById('password').value})
  });
  if (r.ok) { window.location.href = '/'; }
  else { document.getElementById('err').style.display = 'block'; }
}
</script></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _get_health(self):
        from checks.health import get_sources_health
        return get_sources_health()

    # --- Data ---
    def _get_stats(self):
        conn = get_connection()
        cur = conn.cursor()
        try:
            # Single query instead of 7+2 separate queries
            cur.execute("SELECT status, COUNT(*) FROM news GROUP BY status")
            stats = {}
            total = 0
            for row in cur.fetchall():
                s, c = (row[0], row[1]) if _is_postgres() else (row[0], row[1])
                stats[s] = c
                total += c
            stats["total"] = total
            # Ensure all expected statuses exist
            for s in ["new", "in_review", "duplicate", "approved", "processed", "rejected", "ready", "moderation"]:
                stats.setdefault(s, 0)
            cur.execute("SELECT COUNT(*) FROM news_analysis")
            stats["analyzed"] = cur.fetchone()[0]
            return stats
        finally:
            cur.close()

    def _get_news(self):
        from api.news import get_news
        qs = parse_qs(urlparse(self.path).query)
        return get_news(qs)

    def _get_final(self):
        from api.news import get_final
        qs = parse_qs(urlparse(self.path).query)
        return get_final(qs)

    def _get_editorial(self):
        from api.news import get_editorial
        qs = parse_qs(urlparse(self.path).query)
        return get_editorial(qs)

    def _get_news_unified(self):
        from api.news import get_news_unified
        qs = parse_qs(urlparse(self.path).query)
        return get_news_unified(qs)

    def _get_trash(self):
        from api.news import get_trash
        qs = parse_qs(urlparse(self.path).query)
        return get_trash(qs)

    def _get_event_chain_by_id(self, news_id):
        from api.news import get_event_chain_by_id
        return get_event_chain_by_id(news_id)

    def _get_sources(self):
        from api.settings import get_sources
        return get_sources()

    def _get_prompts(self):
        from api.settings import get_prompts
        return get_prompts()

    def _get_settings(self):
        from api.settings import get_settings
        return get_settings()

    # --- Actions ---
    def _run_process(self):
        try:
            from scheduler import process_news
            threading.Thread(target=process_news, daemon=True).start()
            self._json({"status": "ok", "message": "Processing started in background"})
        except Exception as e:
            self._json({"status": "error", "message": str(e)})

    def _process_one(self, body):
        news_id = body.get("news_id")
        if not news_id:
            self._json({"status": "error", "message": "news_id required"})
            return
        try:
            from scheduler import _process_single_news
            result = _process_single_news(news_id)
            self._json({"status": "ok", "result": result})
        except Exception as e:
            self._json({"status": "error", "message": str(e)})

    def _export_sheets(self, body):
        from api.news import export_sheets
        self._json(export_sheets(body))

    def _add_source(self, body):
        from api.settings import add_source
        self._json(add_source(body))

    def _edit_source(self, body):
        from api.settings import edit_source
        self._json(edit_source(body))

    def _delete_source(self, body):
        from api.settings import delete_source
        self._json(delete_source(body))

    def _handle_toggle_source(self, body):
        name = body.get("name", "")
        enabled = body.get("enabled", True)
        if not name:
            self._json({"error": "name required"}, 400)
            return
        from core.feature_flags import toggle_source
        toggle_source(name, enabled)
        self._json({"status": "ok", "source": name, "enabled": enabled})

    def _save_prompts(self, body):
        from api.settings import save_prompts
        self._json(save_prompts(body))

    def _save_settings(self, body):
        if not self._require_perm("settings"):
            return
        from api.settings import save_settings
        user = self._get_session_user() or "admin"
        self._json(save_settings(body, user=user))

    def _test_llm(self, body):
        from api.settings import test_llm
        self._json(test_llm(body))

    def _test_keyso(self, body):
        from api.settings import test_keyso
        self._json(test_keyso(body))

    def _quick_tags(self, body):
        from api.news import quick_tags
        self._json(quick_tags(body))

    def _dashboard_groups(self):
        from api.news import dashboard_groups
        qs = parse_qs(urlparse(self.path).query)
        self._json(dashboard_groups(qs))

    def _run_review(self, body):
        from api.news import run_review
        self._json(run_review(body))

    def _review_batch(self, body):
        from api.news import review_batch
        self._json(review_batch(body))

    def _run_auto_review(self, body):
        from api.news import run_auto_review
        self._json(run_auto_review(body))

    def _approve_news(self, body):
        from api.news import approve_news
        self._json(approve_news(body))

    def _reject_news(self, body):
        from api.news import reject_news
        self._json(reject_news(body))

    def _export_all_processed(self, body):
        from api.news import export_all_processed
        self._json(export_all_processed(body))

    def _export_ready_all(self, body):
        from api.news import export_ready_all
        self._json(export_ready_all(body))

    def _pipeline_full_auto(self, body):
        """Режим 1: Полный автомат — score → >70 на LLM → финальный скор → >60 на рерайт → Sheets/Ready."""
        if not self._require_perm("pipeline"):
            return
        news_ids = body.get("news_ids", [])
        select_all = body.get("all_new", False)

        if select_all and not news_ids:
            # Выбрать все new/in_review (исключая уже обработанные)
            conn = get_connection()
            cur = conn.cursor()
            try:
                cur.execute("SELECT id FROM news WHERE status IN ('new', 'in_review') AND status NOT IN ('duplicate', 'rejected', 'ready', 'processed') ORDER BY parsed_at DESC LIMIT 5000")
                if _is_postgres():
                    news_ids = [r[0] for r in cur.fetchall()]
                else:
                    news_ids = [r["id"] for r in cur.fetchall()]
            finally:
                cur.close()

        if not news_ids:
            self._json({"status": "error", "message": "Нет новостей для обработки"})
            return

        # Create tasks
        from scheduler import _create_task, run_full_auto_pipeline
        task_ids = []
        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"
            for nid in news_ids:
                try:
                    cur.execute(f"SELECT title FROM news WHERE id = {ph}", (nid,))
                    row = cur.fetchone()
                    title = (row[0] if _is_postgres() else row["title"]) if row else ""
                except Exception:
                    title = ""
                tid = _create_task("full_auto", nid, title)
                task_ids.append(tid)
        finally:
            cur.close()

        # Run in background
        import threading
        threading.Thread(
            target=run_full_auto_pipeline,
            args=(list(news_ids), list(task_ids)),
            daemon=True
        ).start()

        self._json({"status": "ok", "queued": len(news_ids), "task_ids": task_ids})

    def _pipeline_no_llm(self, body):
        """Режим 2: Без LLM — score → Sheets/NotReady + Модерация."""
        if not self._require_perm("pipeline"):
            return
        news_ids = body.get("news_ids", [])
        select_all = body.get("all_new", False)

        if select_all and not news_ids:
            conn = get_connection()
            cur = conn.cursor()
            try:
                cur.execute("SELECT id FROM news WHERE status IN ('new', 'in_review') AND status NOT IN ('duplicate', 'rejected', 'ready', 'processed') ORDER BY parsed_at DESC LIMIT 5000")
                if _is_postgres():
                    news_ids = [r[0] for r in cur.fetchall()]
                else:
                    news_ids = [r["id"] for r in cur.fetchall()]
            finally:
                cur.close()

        if not news_ids:
            self._json({"status": "error", "message": "Нет новостей для обработки"})
            return

        from scheduler import _create_task, run_no_llm_pipeline
        task_ids = []
        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"
            for nid in news_ids:
                try:
                    cur.execute(f"SELECT title FROM news WHERE id = {ph}", (nid,))
                    row = cur.fetchone()
                    title = (row[0] if _is_postgres() else row["title"]) if row else ""
                except Exception:
                    title = ""
                tid = _create_task("no_llm", nid, title)
                task_ids.append(tid)
        finally:
            cur.close()

        import threading
        threading.Thread(
            target=run_no_llm_pipeline,
            args=(list(news_ids), list(task_ids)),
            daemon=True
        ).start()

        self._json({"status": "ok", "queued": len(news_ids), "task_ids": task_ids})

    def _pipeline_stop(self, body):
        """Остановка текущего пайплайна + отмена pending задач в БД."""
        from scheduler import pipeline_stop
        pipeline_stop()
        # Cancel all pending/running pipeline tasks in DB
        conn = get_connection()
        cur = conn.cursor()
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            if _is_postgres():
                cur.execute(
                    "UPDATE task_queue SET status = 'cancelled', result = %s, updated_at = %s "
                    "WHERE status IN ('pending', 'running') AND task_type IN ('full_auto', 'no_llm')",
                    ('{"reason":"Остановлено пользователем"}', now))
                cancelled = cur.rowcount
            else:
                cur.execute(
                    "UPDATE task_queue SET status = 'cancelled', result = ?, updated_at = ? "
                    "WHERE status IN ('pending', 'running') AND task_type IN ('full_auto', 'no_llm')",
                    ('{"reason":"Остановлено пользователем"}', now))
                cancelled = cur.rowcount
                conn.commit()
        finally:
            cur.close()
        self._json({"status": "ok", "message": f"Остановлено, отменено задач: {cancelled}"})

    def _get_pipeline_status(self):
        """Возвращает текущий статус пайплайна (running tasks)."""
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT task_type, status, COUNT(*) as cnt
                FROM task_queue
                WHERE status IN ('pending', 'running')
                GROUP BY task_type, status
            """)
            if _is_postgres():
                rows = [{"type": r[0], "status": r[1], "count": r[2]} for r in cur.fetchall()]
            else:
                rows = [{"type": r[0], "status": r[1], "count": r[2]} for r in cur.fetchall()]

            # Any running pipeline?
            running = any(r["status"] == "running" for r in rows)
            pending = sum(r["count"] for r in rows if r["status"] == "pending")
            running_count = sum(r["count"] for r in rows if r["status"] == "running")
            # Determine active type
            active_type = ""
            for r in rows:
                if r["status"] == "running" and r["type"] in ("full_auto", "no_llm"):
                    active_type = r["type"]
                    break
            if not active_type:
                for r in rows:
                    if r["status"] == "pending" and r["type"] in ("full_auto", "no_llm"):
                        active_type = r["type"]
                        break

            # Count done tasks and find earliest running start
            total_done = 0
            started_at = ""
            try:
                cur.execute("""
                    SELECT COUNT(*) FROM task_queue
                    WHERE status = 'done' AND task_type IN ('full_auto', 'no_llm')
                """)
                total_done = cur.fetchone()[0]
                cur.execute("""
                    SELECT MIN(created_at) FROM task_queue
                    WHERE status IN ('pending', 'running') AND task_type IN ('full_auto', 'no_llm')
                """)
                row = cur.fetchone()
                started_at = str(row[0]) if row and row[0] else ""
            except Exception:
                pass

            return {
                "running": running or pending > 0,
                "active_type": active_type,
                "running_count": running_count,
                "pending_count": pending,
                "total_done": total_done,
                "started_at": started_at,
                "details": rows,
            }
        finally:
            cur.close()

    def _get_moderation_list(self):
        from api.news import get_moderation_list
        qs = parse_qs(urlparse(self.path).query)
        return get_moderation_list(qs)

    def _get_moderation(self, body):
        from api.news import get_moderation_list
        self._json(get_moderation_list({}))

    def _seo_check(self, body):
        from api.news import seo_check
        self._json(seo_check(body))

    def _moderation_rewrite(self, body):
        from api.news import moderation_rewrite
        self._json(moderation_rewrite(body))

    def _test_sheets(self, body):
        from api.settings import test_sheets
        self._json(test_sheets(body))

    def _reparse_source(self, body):
        from api.settings import reparse_source
        self._json(reparse_source(body))

    def _heal_source(self, body):
        from api.settings import heal_source
        self._json(heal_source(body))

    def _cleanup_short(self, body):
        min_chars = body.get("min_chars", 100)
        from api.news import cleanup_short_news
        count = cleanup_short_news(min_chars)
        self._json({"status": "ok", "deleted": count})

    def _cleanup_old(self, body):
        days = body.get("days", 7)
        from api.news import cleanup_old_news
        count = cleanup_old_news(days)
        self._json({"status": "ok", "deleted": count})

    def _export_trash_sheets(self, body):
        from api.news import export_trash_to_sheets
        self._json(export_trash_to_sheets())

    def _change_password(self, body):
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            self._json({"status": "error", "message": "Username and password required"})
            return
        if username not in USERS:
            self._json({"status": "error", "message": "User not found"})
            return
        USERS[username] = hashlib.sha256(password.encode()).hexdigest()
        self._json({"status": "ok"})

    def _bulk_status(self, body):
        from api.news import bulk_status
        self._json(bulk_status(body))

    def _delete_news(self, body):
        if not self._require_perm("delete"):
            return
        from api.news import delete_news
        self._json(delete_news(body))

    def _restore_news(self, body):
        if not self._require_perm("delete"):
            return
        from api.news import restore_news
        self._json(restore_news(body))

    def _purge_news(self, body):
        if not self._require_perm("delete"):
            return
        from api.news import purge_news
        self._json(purge_news(body))

    def _restore_article(self, body):
        if not self._require_perm("delete"):
            return
        from api.articles import restore_article
        self._json(restore_article(body))

    def _test_parse(self, body):
        from api.settings import test_parse
        self._json(test_parse(body))

    def _setup_headers(self, body):
        from api.settings import setup_headers
        self._json(setup_headers(body))

    def _reparse_all(self, body):
        from api.settings import reparse_all
        self._json(reparse_all(body))

    def _get_sources_stats(self):
        from api.settings import get_sources_stats
        return get_sources_stats()
    def _get_db_info(self):
        from api.settings import get_db_info
        return get_db_info()
    def _export_sheets_bulk(self, body):
        from api.news import export_sheets_bulk
        self._json(export_sheets_bulk(body))

    def _rewrite_news(self, body):
        from api.articles import rewrite_news_handler
        self._json(rewrite_news_handler(body))

    def _merge_news(self, body):
        from api.news import merge_news
        self._json(merge_news(body))

    def _news_detail(self, body):
        from api.news import news_detail
        self._json(news_detail(body))

    def _analyze_news(self, body):
        from api.news import analyze_news
        self._json(analyze_news(body))

    def _batch_rewrite(self, body):
        from api.articles import batch_rewrite
        self._json(batch_rewrite(body))
    # ---- Analytics methods ----

    def _get_analytics(self):
        from api.analytics import get_analytics
        return get_analytics()
    def _get_prompt_versions(self):
        from api.settings import get_prompt_versions
        return get_prompt_versions()
    def _preview_rewrite(self, body):
        from api.settings import preview_rewrite
        self._json(preview_rewrite(body))

    def _save_prompt_version(self, body):
        from api.settings import save_prompt_version
        self._json(save_prompt_version(body))
    def _activate_prompt_version(self, body):
        from api.settings import activate_prompt_version
        self._json(activate_prompt_version(body))
    def _generate_digest(self, body):
        from api.settings import generate_digest
        self._json(generate_digest(body))

    def _get_digests(self):
        from api.settings import get_digests
        return get_digests()

    def _generate_and_save_digest(self, body):
        from api.settings import generate_and_save_digest
        self._json(generate_and_save_digest(body))

    def _get_event_chain(self, body):
        from api.news import get_event_chain
        self._json(get_event_chain(body))

    def _get_queue(self):
        from api.queue import get_queue
        qs = parse_qs(urlparse(self.path).query)
        status_filter = qs.get("status", [""])[0]
        task_type_filter = qs.get("task_type", [""])[0]
        return get_queue(status_filter=status_filter, task_type_filter=task_type_filter)

    def _cancel_queue_task(self, body):
        from api.queue import cancel_queue_task
        self._json(cancel_queue_task(body))

    def _cancel_all_queue(self, body):
        from api.queue import cancel_all_queue
        self._json(cancel_all_queue(body))

    def _clear_done_queue(self, body):
        from api.queue import clear_done_queue
        self._json(clear_done_queue(body))

    def _retry_queue_tasks(self, body):
        from api.queue import retry_queue_tasks
        self._json(retry_queue_tasks(body))

    def _get_viral_triggers(self):
        from api.viral import get_viral_triggers
        return get_viral_triggers()

    def _save_viral_trigger(self, body):
        from api.viral import save_viral_trigger
        self._json(save_viral_trigger(body))

    def _delete_viral_trigger(self, body):
        from api.viral import delete_viral_trigger
        self._json(delete_viral_trigger(body))

    def _queue_batch_rewrite(self, body):
        from api.queue import queue_batch_rewrite
        self._json(queue_batch_rewrite(body))

    def _queue_sheets_export(self, body):
        from api.queue import queue_sheets_export
        self._json(queue_sheets_export(body))

    def _serve_docx_bulk(self, article_ids):
        """Генерирует ZIP с несколькими DOCX файлами."""
        import io
        import json
        import zipfile
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for aid in article_ids:
                    cur.execute(f"SELECT * FROM articles WHERE id = {ph}", (aid,))
                    row = cur.fetchone()
                    if not row:
                        continue
                    if _is_postgres():
                        columns = [desc[0] for desc in cur.description]
                        article = dict(zip(columns, row))
                    else:
                        article = dict(row)

                    doc = Document()
                    style = doc.styles['Normal']
                    style.font.name = 'Calibri'
                    style.font.size = Pt(11)

                    doc.add_heading(article.get("title", ""), level=1)

                    meta_p = doc.add_paragraph()
                    run = meta_p.add_run(f"Стиль: {article.get('style', '')} | Язык: {article.get('language', '')}")
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(128, 128, 128)
                    if article.get("source_url"):
                        run2 = meta_p.add_run(f"\nИсточник: {article['source_url']}")
                        run2.font.size = Pt(9)
                        run2.font.color.rgb = RGBColor(128, 128, 128)

                    if article.get("seo_title") or article.get("seo_description"):
                        doc.add_heading("SEO", level=2)
                        if article.get("seo_title"):
                            p = doc.add_paragraph()
                            p.add_run("Title: ").bold = True
                            p.add_run(article["seo_title"])
                        if article.get("seo_description"):
                            p = doc.add_paragraph()
                            p.add_run("Description: ").bold = True
                            p.add_run(article["seo_description"])

                    tags = []
                    try:
                        tags = json.loads(article.get("tags", "[]"))
                    except Exception:
                        pass
                    if tags:
                        p = doc.add_paragraph()
                        p.add_run("Теги: ").bold = True
                        p.add_run(", ".join(tags))

                    doc.add_paragraph("")
                    doc.add_heading("Текст статьи", level=2)
                    text = article.get("text", "")
                    for paragraph in text.split("\n"):
                        paragraph = paragraph.strip()
                        if paragraph:
                            if paragraph.startswith("## "):
                                doc.add_heading(paragraph[3:], level=3)
                            elif paragraph.startswith("# "):
                                doc.add_heading(paragraph[2:], level=2)
                            else:
                                doc.add_paragraph(paragraph)

                    doc_buffer = io.BytesIO()
                    doc.save(doc_buffer)
                    safe_title = "".join(c for c in article.get("title", "article")[:40] if c.isalnum() or c in " _-").strip() or "article"
                    zf.writestr(f"{safe_title}.docx", doc_buffer.getvalue())

            data = zip_buffer.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="articles.zip"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        finally:
            cur.close()
    # --- Articles ---
    def _get_articles(self):
        from api.articles import get_articles
        return get_articles()
    def _save_article(self, body):
        from api.articles import save_article
        self._json(save_article(body))
    def _update_article(self, body):
        from api.articles import update_article
        self._json(update_article(body, changed_by=self._get_session_user() or "admin"))
    def _delete_article(self, body):
        from api.articles import delete_article
        self._json(delete_article(body))
    def _article_detail(self, body):
        from api.articles import article_detail
        self._json(article_detail(body))
    def _schedule_article(self, body):
        from api.articles import schedule_article
        self._json(schedule_article(body))
    def _rewrite_article(self, body):
        from api.articles import rewrite_article
        self._json(rewrite_article(body))
    def _improve_article(self, body):
        from api.articles import improve_article
        self._json(improve_article(body))
    def _serve_news_xlsx(self):
        """Generate and serve XLSX export of filtered news."""
        from urllib.parse import parse_qs, urlparse
        from api.news import export_news_xlsx
        try:
            qs = parse_qs(urlparse(self.path).query)
            xlsx_bytes = export_news_xlsx(qs)
            from datetime import datetime
            fname = f"igronews_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(xlsx_bytes)))
            self.end_headers()
            self.wfile.write(xlsx_bytes)
        except Exception as e:
            logger.error("XLSX export error: %s", e)
            self._json({"status": "error", "message": str(e)}, 500)

    def _serve_articles_xlsx(self):
        """Generate and serve CMS-ready XLSX export of articles."""
        from urllib.parse import parse_qs, urlparse
        from api.articles import export_articles_xlsx
        try:
            qs = parse_qs(urlparse(self.path).query)
            xlsx_bytes = export_articles_xlsx(qs)
            from datetime import datetime
            fname = f"igronews_cms_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(xlsx_bytes)))
            self.end_headers()
            self.wfile.write(xlsx_bytes)
        except Exception as e:
            logger.error("Articles XLSX export error: %s", e)
            self._json({"status": "error", "message": str(e)}, 500)

    def _serve_docx(self, article_id):
        """Генерация и отдача DOCX файла."""
        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"
            cur.execute(f"SELECT * FROM articles WHERE id = {ph}", (article_id,))
            row = cur.fetchone()
            if not row:
                self.send_response(404)
                self.end_headers()
                return
            if _is_postgres():
                columns = [desc[0] for desc in cur.description]
                article = dict(zip(columns, row))
            else:
                article = dict(row)

            import io
            import json
            from docx import Document
            from docx.shared import Pt, Inches, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            doc = Document()
            style = doc.styles['Normal']
            style.font.name = 'Calibri'
            style.font.size = Pt(11)

            # Title
            title_p = doc.add_heading(article.get("title", ""), level=1)
            title_p.alignment = WD_ALIGN_PARAGRAPH.LEFT

            # Meta info block
            meta_p = doc.add_paragraph()
            meta_p.paragraph_format.space_after = Pt(6)
            run = meta_p.add_run(f"Стиль: {article.get('style', '')} | Язык: {article.get('language', '')}")
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(128, 128, 128)
            if article.get("source_url"):
                run2 = meta_p.add_run(f"\nИсточник: {article['source_url']}")
                run2.font.size = Pt(9)
                run2.font.color.rgb = RGBColor(128, 128, 128)

            # SEO block
            if article.get("seo_title") or article.get("seo_description"):
                doc.add_heading("SEO", level=2)
                if article.get("seo_title"):
                    p = doc.add_paragraph()
                    p.add_run("Title: ").bold = True
                    p.add_run(article["seo_title"])
                if article.get("seo_description"):
                    p = doc.add_paragraph()
                    p.add_run("Description: ").bold = True
                    p.add_run(article["seo_description"])

            # Tags
            tags = []
            try:
                tags = json.loads(article.get("tags", "[]"))
            except Exception:
                pass
            if tags:
                p = doc.add_paragraph()
                p.add_run("Теги: ").bold = True
                p.add_run(", ".join(tags))

            doc.add_paragraph("")  # spacer

            # Article text
            doc.add_heading("Текст статьи", level=2)
            text = article.get("text", "")
            for paragraph in text.split("\n"):
                paragraph = paragraph.strip()
                if paragraph:
                    if paragraph.startswith("## "):
                        doc.add_heading(paragraph[3:], level=3)
                    elif paragraph.startswith("# "):
                        doc.add_heading(paragraph[2:], level=2)
                    else:
                        doc.add_paragraph(paragraph)

            # Original text if exists
            if article.get("original_text"):
                doc.add_page_break()
                doc.add_heading("Оригинал", level=2)
                orig_p = doc.add_paragraph()
                if article.get("original_title"):
                    run = orig_p.add_run(article["original_title"] + "\n\n")
                    run.bold = True
                for line in article["original_text"][:3000].split("\n"):
                    line = line.strip()
                    if line:
                        p = doc.add_paragraph(line)
                        for run in p.runs:
                            run.font.color.rgb = RGBColor(128, 128, 128)
                            run.font.size = Pt(10)

            buffer = io.BytesIO()
            doc.save(buffer)
            data = buffer.getvalue()

            safe_title = "".join(c for c in article.get("title", "article")[:40] if c.isalnum() or c in " _-").strip() or "article"
            filename = f"{safe_title}.docx"

            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        finally:
            cur.close()
    # --- Logs, Cache, Rate, Translate, AI ---

    def _get_viral(self):
        from api.viral import get_viral
        qs = parse_qs(urlparse(self.path).query)
        return get_viral(qs)

    def _get_logs(self):
        from api.settings import get_logs
        qs = parse_qs(urlparse(self.path).query)
        return get_logs(query_params=qs)

    def _get_rate_stats(self):
        from api.settings import get_rate_stats
        return get_rate_stats()

    def _get_cache_stats(self):
        from api.settings import get_cache_stats
        return get_cache_stats()

    def _clear_cache(self, body):
        from api.settings import clear_cache
        self._json(clear_cache(body))

    def _translate_title(self, body):
        from api.news import translate_title
        self._json(translate_title(body))

    def _ai_recommend(self, body):
        from api.news import ai_recommend
        self._json(ai_recommend(body))

    def _get_funnel_analytics(self):
        from api.analytics import get_funnel_analytics
        return get_funnel_analytics()

    def _get_cost_by_source(self):
        from api.analytics import get_cost_by_source
        return get_cost_by_source()

    def _get_prompt_insights(self):
        from api.analytics import get_prompt_insights
        return get_prompt_insights()

    # --- Phase 2: Content Versioning & Multi-Output ---

    def _get_article_versions(self, body):
        from api.articles import get_article_versions
        self._json(get_article_versions(body))

    def _save_article_version(self, article_id, article_data, change_type="manual", changed_by="system"):
        from api.articles import save_article_version
        save_article_version(article_id, article_data, change_type=change_type, changed_by=changed_by)

    def _generate_multi_output(self, body):
        from api.articles import generate_multi_output
        self._json(generate_multi_output(body))

    def _regenerate_field(self, body):
        from api.articles import regenerate_field
        self._json(regenerate_field(body))

    # --- Phase 0: Feature Flags & Observability API ---

    def _get_feature_flags(self):
        from api.settings import get_feature_flags
        return get_feature_flags()

    def _toggle_feature_flag(self, body):
        if not self._require_perm("flags"):
            return
        from api.settings import toggle_feature_flag
        user = self._get_session_user() or "admin"
        self._json(toggle_feature_flag(body, user=user))

    def _get_cost_summary(self):
        from api.analytics import get_cost_summary
        return get_cost_summary()

    def _get_config_audit(self):
        from api.settings import get_config_audit
        return get_config_audit()

    def _get_decision_trace(self, body):
        from api.settings import get_decision_trace
        self._json(get_decision_trace(body))

    def _get_ops_dashboard(self):
        from api.dashboard import get_ops_dashboard
        return get_ops_dashboard()

    # --- Phase 4: Storylines, Source Health Plus, Threshold Simulator ---

    def _get_storylines(self):
        from api.dashboard import get_storylines
        qs = parse_qs(urlparse(self.path).query)
        days = int(qs.get("days", ["3"])[0])
        return get_storylines(days=days)

    def _get_source_health_plus(self):
        from api.dashboard import get_source_health_plus
        return get_source_health_plus()

    def _simulate_thresholds(self, body):
        from api.dashboard import simulate_thresholds
        self._json(simulate_thresholds(body))

    def _export_storylines(self, body):
        from api.dashboard import export_storylines_to_sheets
        days = int(body.get("days", 3))
        self._json(export_storylines_to_sheets(days=days))

    def _get_storylines_settings(self):
        from api.dashboard import get_storylines_settings
        return get_storylines_settings()

    def _get_storylines_history(self):
        from api.dashboard import get_storylines_export_history
        return get_storylines_export_history()

    def _save_storylines_settings(self, body):
        from api.dashboard import save_storylines_settings
        self._json(save_storylines_settings(body))

    def _rescore_news(self, body):
        if not self._require_perm("pipeline"):
            return
        from api.news import rescore_news
        self._json(rescore_news(body))

    def _serve_dashboard(self):
        global _DASHBOARD_HTML_GZIP, _DASHBOARD_HTML_BYTES
        # Pre-compress dashboard HTML once (saves ~400KB per request)
        if _DASHBOARD_HTML_BYTES is None:
            _DASHBOARD_HTML_BYTES = DASHBOARD_HTML.encode()
            _DASHBOARD_HTML_GZIP = gzip.compress(_DASHBOARD_HTML_BYTES, compresslevel=9)
            logger.info("Dashboard HTML compressed: %dKB -> %dKB (%.0f%% saved)",
                        len(_DASHBOARD_HTML_BYTES) // 1024, len(_DASHBOARD_HTML_GZIP) // 1024,
                        (1 - len(_DASHBOARD_HTML_GZIP) / len(_DASHBOARD_HTML_BYTES)) * 100)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # Allow browser to cache for 5 min, revalidate after
        self.send_header("Cache-Control", "public, max-age=300, must-revalidate")
        if self._accepts_gzip():
            body = _DASHBOARD_HTML_GZIP
            self.send_header("Content-Encoding", "gzip")
        else:
            body = _DASHBOARD_HTML_BYTES
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# Dashboard HTML loaded from static file
def _load_dashboard_html():
    import os
    html_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

DASHBOARD_HTML = _load_dashboard_html()



def _load_active_prompts():
    """Загружает активные версии промптов из БД при старте."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT prompt_name, content FROM prompt_versions WHERE is_active = 1")
            import apis.llm as llm
            prompt_map = {
                "trend_forecast": "PROMPT_TREND_FORECAST",
                "merge_analysis": "PROMPT_MERGE_ANALYSIS",
                "keyso_queries": "PROMPT_KEYSO_QUERIES",
                "rewrite": "PROMPT_REWRITE",
            }
            if _is_postgres():
                for row in cur.fetchall():
                    attr = prompt_map.get(row[0])
                    if attr and hasattr(llm, attr):
                        setattr(llm, attr, row[1])
                        logger.info("Loaded active prompt: %s", row[0])
            else:
                for row in cur.fetchall():
                    r = dict(row)
                    attr = prompt_map.get(r["prompt_name"])
                    if attr and hasattr(llm, attr):
                        setattr(llm, attr, r["content"])
                        logger.info("Loaded active prompt: %s", r["prompt_name"])
        finally:
            cur.close()
    except Exception as e:
        logger.debug("Could not load active prompts: %s", e)


def start_web():
    _load_active_prompts()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AdminHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Admin panel running on port %d", PORT)
    return server
