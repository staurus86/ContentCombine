"""Settings and tools API functions extracted from web.py."""

import logging

from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


# --------------- GET endpoints (return dicts) ---------------

def get_sources():
    import config
    return config.SOURCES


def get_prompts():
    from apis.llm import PROMPT_TREND_FORECAST, PROMPT_MERGE_ANALYSIS, PROMPT_KEYSO_QUERIES, PROMPT_REWRITE, REWRITE_STYLES
    return {
        "trend_forecast": PROMPT_TREND_FORECAST,
        "merge_analysis": PROMPT_MERGE_ANALYSIS,
        "keyso_queries": PROMPT_KEYSO_QUERIES,
        "rewrite": PROMPT_REWRITE,
        "rewrite_styles": {k: v["instructions"] for k, v in REWRITE_STYLES.items()},
    }


def _mask(val: str, keep: int = 8) -> str:
    """Mask sensitive value, showing first and last chars."""
    if not val:
        return ""
    if len(val) <= keep:
        return "*" * len(val)
    show = min(4, max(2, keep // 2))
    mid = min(20, len(val) - show * 2)  # cap masked section to avoid UI overflow
    return val[:show] + "*" * mid + val[-show:]


def get_settings():
    import config
    return {
        # API & LLM
        "llm_model": config.LLM_MODEL,
        "openai_base_url": getattr(config, "OPENAI_BASE_URL", ""),
        "llm_temperature": getattr(config, "LLM_TEMPERATURE", 0.3),
        "llm_timeout_seconds": getattr(config, "LLM_TIMEOUT_SECONDS", 45),
        "keyso_region": getattr(config, "KEYSO_REGION", "ru"),
        "regions": config.REGIONS,
        # Google Sheets
        "sheets_id": config.GOOGLE_SHEETS_ID,
        "sheets_tab": config.SHEETS_TAB,
        "sheets_tab_ready": getattr(config, "SHEETS_TAB_READY", "Ready"),
        "sheets_tab_not_ready": getattr(config, "SHEETS_TAB_NOT_READY", "NotReady"),
        "sheets_batch_size": getattr(config, "SHEETS_BATCH_SIZE", 25),
        "sheets_min_api_interval": getattr(config, "SHEETS_MIN_API_INTERVAL", 1.2),
        "sheets_client_ttl": getattr(config, "SHEETS_CLIENT_TTL", 3000),
        # API keys (masked for display)
        "openai_key_set": bool(config.OPENAI_API_KEY),
        "openai_key_masked": _mask(config.OPENAI_API_KEY),
        "keyso_key_set": bool(config.KEYSO_API_KEY),
        "keyso_key_masked": _mask(config.KEYSO_API_KEY),
        "google_sa_set": bool(config.GOOGLE_SERVICE_ACCOUNT_JSON),
        "google_sa_masked": _mask(config.GOOGLE_SERVICE_ACCOUNT_JSON, 20),
        "vk_token_set": bool(getattr(config, "VK_API_TOKEN", "")),
        "vk_token_masked": _mask(getattr(config, "VK_API_TOKEN", "")),
        "tg_bot_token_set": bool(getattr(config, "TELEGRAM_BOT_TOKEN", "")),
        "tg_bot_token_masked": _mask(getattr(config, "TELEGRAM_BOT_TOKEN", "")),
        # Automation
        "auto_approve_threshold": getattr(config, "AUTO_APPROVE_THRESHOLD", 0),
        "auto_rewrite_on_publish_now": getattr(config, "AUTO_REWRITE_ON_PUBLISH_NOW", True),
        "auto_rewrite_style": getattr(config, "AUTO_REWRITE_STYLE", "news"),
        # Scoring weights
        "score_weight_internal": getattr(config, "SCORE_WEIGHT_INTERNAL", 0.4),
        "score_weight_viral": getattr(config, "SCORE_WEIGHT_VIRAL", 0.2),
        "score_weight_keyso": getattr(config, "SCORE_WEIGHT_KEYSO", 0.15),
        "score_weight_trends": getattr(config, "SCORE_WEIGHT_TRENDS", 0.1),
        "score_weight_headline": getattr(config, "SCORE_WEIGHT_HEADLINE", 0.15),
        # Pipeline thresholds
        "full_auto_score_threshold": getattr(config, "FULL_AUTO_SCORE_THRESHOLD", 70),
        "full_auto_final_threshold": getattr(config, "FULL_AUTO_FINAL_THRESHOLD", 60),
        "auto_export_threshold": getattr(config, "AUTO_EXPORT_THRESHOLD", 60),
        "auto_reject_score_threshold": getattr(config, "AUTO_REJECT_SCORE_THRESHOLD", 15),
        "publish_spacing_minutes": getattr(config, "PUBLISH_SPACING_MINUTES", 15),
        # Viral thresholds
        "viral_high_threshold": getattr(config, "VIRAL_HIGH_THRESHOLD", 70),
        "viral_medium_threshold": getattr(config, "VIRAL_MEDIUM_THRESHOLD", 40),
        "viral_low_threshold": getattr(config, "VIRAL_LOW_THRESHOLD", 20),
        # Retention
        "deleted_news_retention_days": getattr(config, "DELETED_NEWS_RETENTION_DAYS", 30),
        "plaintext_retention_days": getattr(config, "PLAINTEXT_RETENTION_DAYS", 7),
        "health_log_retention_days": getattr(config, "HEALTH_LOG_RETENTION_DAYS", 7),
        # Cron schedule
        "auto_rescore_cron_hour": getattr(config, "AUTO_RESCORE_CRON_HOUR", 4),
        "auto_digest_cron_hour": getattr(config, "AUTO_DIGEST_CRON_HOUR", 23),
        "storylines_export_cron_hour": getattr(config, "STORYLINES_EXPORT_CRON_HOUR", 9),
        # Batch sizes
        "news_batch_fetch_limit": getattr(config, "NEWS_BATCH_FETCH_LIMIT", 20),
        "vk_post_max_age_days": getattr(config, "VK_POST_MAX_AGE_DAYS", 7),
        "vk_posts_batch_size": getattr(config, "VK_POSTS_BATCH_SIZE", 20),
        "telegram_post_max_age_days": getattr(config, "TELEGRAM_POST_MAX_AGE_DAYS", 7),
        "telegram_messages_batch_size": getattr(config, "TELEGRAM_MESSAGES_BATCH_SIZE", 20),
        # System health
        "watchdog_stale_timeout": getattr(config, "WATCHDOG_STALE_TIMEOUT", 300),
        "source_failure_threshold": getattr(config, "SOURCE_FAILURE_THRESHOLD", 5),
        "source_probe_cooldown": getattr(config, "SOURCE_PROBE_COOLDOWN", 600),
        "zombie_threads_critical": getattr(config, "ZOMBIE_THREADS_CRITICAL", 5),
    }


def get_sources_stats():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT source, COUNT(*) as cnt, MAX(parsed_at) as last_parsed FROM news GROUP BY source ORDER BY cnt DESC")
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()


def get_db_info():
    conn = get_connection()
    cur = conn.cursor()
    try:
        info = {"type": "PostgreSQL" if _is_postgres() else "SQLite"}
        cur.execute("SELECT COUNT(*) FROM news")
        info["total_news"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM news_analysis")
        info["total_analyzed"] = cur.fetchone()[0]
        for status in ["new", "in_review", "approved", "processed", "rejected", "duplicate"]:
            ph = "%s" if _is_postgres() else "?"
            cur.execute(f"SELECT COUNT(*) FROM news WHERE status = {ph}", (status,))
            info[f"status_{status}"] = cur.fetchone()[0]
        cur.execute("SELECT MIN(parsed_at), MAX(parsed_at) FROM news")
        row = cur.fetchone()
        info["oldest"] = str(row[0]) if row[0] else "-"
        info["newest"] = str(row[1]) if row[1] else "-"
        return info
    finally:
        cur.close()


def get_prompt_versions():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM prompt_versions ORDER BY prompt_name, version DESC")
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
        return {"status": "ok", "versions": rows}
    finally:
        cur.close()


def get_digests():
    """Returns last 10 saved digests."""
    from storage.database import get_digests as _get_digests
    digests = _get_digests(limit=10)
    return {"status": "ok", "digests": digests}


def get_feature_flags():
    try:
        from core.feature_flags import get_all_flags
        return {"flags": get_all_flags()}
    except Exception as e:
        return {"status": "error", "message": str(e), "flags": []}


def get_config_audit():
    try:
        from core.observability import get_config_audit
        return {"audit": get_config_audit(limit=50)}
    except Exception as e:
        return {"status": "error", "message": str(e), "audit": []}


def get_logs(query_params=None):
    """Get logs. query_params is a dict from parse_qs (values are lists)."""
    if query_params is None:
        query_params = {}
    limit = int(query_params.get("limit", [100])[0])
    level = query_params.get("level", [""])[0]
    from apis.cache import get_logs
    return {"logs": get_logs(limit=limit, level=level)}


def get_rate_stats():
    from apis.cache import get_rate_stats
    return get_rate_stats()


def get_cache_stats():
    from apis.cache import get_cache_stats
    return get_cache_stats()


# --------------- POST endpoints (return dicts) ---------------

def add_source(body):
    import config
    source = {
        "name": body.get("name", ""),
        "type": body.get("type", "rss"),
        "url": body.get("url", ""),
        "interval": int(body.get("interval", 15)),
    }
    if body.get("selector"):
        source["selector"] = body["selector"]
    config.SOURCES.append(source)
    return {"status": "ok", "sources": config.SOURCES}


def edit_source(body):
    import config
    old_name = body.get("old_name", "")
    for s in config.SOURCES:
        if s["name"] == old_name:
            s["name"] = body.get("name", s["name"])
            s["type"] = body.get("type", s["type"])
            s["url"] = body.get("url", s["url"])
            s["interval"] = int(body.get("interval", s["interval"]))
            if body.get("selector"):
                s["selector"] = body["selector"]
            elif "selector" in s and body.get("type") == "rss":
                del s["selector"]
            break
    return {"status": "ok", "sources": config.SOURCES}


def delete_source(body):
    import config
    name = body.get("name")
    config.SOURCES[:] = [s for s in config.SOURCES if s["name"] != name]
    return {"status": "ok", "sources": config.SOURCES}


def save_prompts(body):
    import apis.llm as llm
    from storage.database import set_app_setting
    if "trend_forecast" in body:
        llm.PROMPT_TREND_FORECAST = body["trend_forecast"]
        set_app_setting("PROMPT_TREND_FORECAST", body["trend_forecast"])
    if "merge_analysis" in body:
        llm.PROMPT_MERGE_ANALYSIS = body["merge_analysis"]
        set_app_setting("PROMPT_MERGE_ANALYSIS", body["merge_analysis"])
    if "keyso_queries" in body:
        llm.PROMPT_KEYSO_QUERIES = body["keyso_queries"]
        set_app_setting("PROMPT_KEYSO_QUERIES", body["keyso_queries"])
    if "rewrite" in body:
        llm.PROMPT_REWRITE = body["rewrite"]
        set_app_setting("PROMPT_REWRITE", body["rewrite"])
    if "rewrite_styles" in body and isinstance(body["rewrite_styles"], dict):
        for style_name, instructions in body["rewrite_styles"].items():
            if style_name in llm.REWRITE_STYLES:
                llm.REWRITE_STYLES[style_name]["instructions"] = instructions
        import json
        set_app_setting("REWRITE_STYLES", json.dumps({k: v["instructions"] for k, v in llm.REWRITE_STYLES.items()}, ensure_ascii=False))
    return {"status": "ok"}


def save_settings(body, user="admin"):
    """Save settings. Caller must check permissions before calling.

    Args:
        body: dict with setting values
        user: username for audit logging
    """
    import config
    changes = []
    if "llm_model" in body and body["llm_model"] != config.LLM_MODEL:
        changes.append(("llm_model", config.LLM_MODEL, body["llm_model"]))
        config.LLM_MODEL = body["llm_model"]
    if "keyso_region" in body and body["keyso_region"] != config.KEYSO_REGION:
        changes.append(("keyso_region", config.KEYSO_REGION, body["keyso_region"]))
        config.KEYSO_REGION = body["keyso_region"]
    if "sheets_tab" in body and body["sheets_tab"] != config.SHEETS_TAB:
        changes.append(("sheets_tab", config.SHEETS_TAB, body["sheets_tab"]))
        config.SHEETS_TAB = body["sheets_tab"]
    if "auto_approve_threshold" in body:
        try:
            new_val = int(body["auto_approve_threshold"])
            if new_val != config.AUTO_APPROVE_THRESHOLD:
                changes.append(("auto_approve_threshold", str(config.AUTO_APPROVE_THRESHOLD), str(new_val)))
                config.AUTO_APPROVE_THRESHOLD = new_val
        except (ValueError, TypeError):
            pass
    if "auto_rewrite_on_publish_now" in body:
        new_val = bool(body["auto_rewrite_on_publish_now"])
        if new_val != config.AUTO_REWRITE_ON_PUBLISH_NOW:
            changes.append(("auto_rewrite_on_publish_now", str(config.AUTO_REWRITE_ON_PUBLISH_NOW), str(new_val)))
            config.AUTO_REWRITE_ON_PUBLISH_NOW = new_val
    if "auto_rewrite_style" in body and body["auto_rewrite_style"] != config.AUTO_REWRITE_STYLE:
        changes.append(("auto_rewrite_style", config.AUTO_REWRITE_STYLE, body["auto_rewrite_style"]))
        config.AUTO_REWRITE_STYLE = body["auto_rewrite_style"]

    # Int settings (generic handler)
    _int_settings = {
        "full_auto_score_threshold": "FULL_AUTO_SCORE_THRESHOLD",
        "full_auto_final_threshold": "FULL_AUTO_FINAL_THRESHOLD",
        "auto_export_threshold": "AUTO_EXPORT_THRESHOLD",
        "auto_reject_score_threshold": "AUTO_REJECT_SCORE_THRESHOLD",
        "publish_spacing_minutes": "PUBLISH_SPACING_MINUTES",
        "llm_timeout_seconds": "LLM_TIMEOUT_SECONDS",
        "viral_high_threshold": "VIRAL_HIGH_THRESHOLD",
        "viral_medium_threshold": "VIRAL_MEDIUM_THRESHOLD",
        "viral_low_threshold": "VIRAL_LOW_THRESHOLD",
        "deleted_news_retention_days": "DELETED_NEWS_RETENTION_DAYS",
        "plaintext_retention_days": "PLAINTEXT_RETENTION_DAYS",
        "health_log_retention_days": "HEALTH_LOG_RETENTION_DAYS",
        "auto_rescore_cron_hour": "AUTO_RESCORE_CRON_HOUR",
        "auto_digest_cron_hour": "AUTO_DIGEST_CRON_HOUR",
        "storylines_export_cron_hour": "STORYLINES_EXPORT_CRON_HOUR",
        "sheets_batch_size": "SHEETS_BATCH_SIZE",
        "news_batch_fetch_limit": "NEWS_BATCH_FETCH_LIMIT",
        "vk_post_max_age_days": "VK_POST_MAX_AGE_DAYS",
        "vk_posts_batch_size": "VK_POSTS_BATCH_SIZE",
        "telegram_post_max_age_days": "TELEGRAM_POST_MAX_AGE_DAYS",
        "telegram_messages_batch_size": "TELEGRAM_MESSAGES_BATCH_SIZE",
        "watchdog_stale_timeout": "WATCHDOG_STALE_TIMEOUT",
        "source_failure_threshold": "SOURCE_FAILURE_THRESHOLD",
        "source_probe_cooldown": "SOURCE_PROBE_COOLDOWN",
        "zombie_threads_critical": "ZOMBIE_THREADS_CRITICAL",
        "sheets_client_ttl": "SHEETS_CLIENT_TTL",
    }
    for body_key, cfg_attr in _int_settings.items():
        if body_key in body:
            try:
                new_val = int(body[body_key])
                old_val = getattr(config, cfg_attr, None)
                if new_val != old_val:
                    changes.append((body_key, str(old_val), str(new_val)))
                    setattr(config, cfg_attr, new_val)
            except (ValueError, TypeError):
                pass

    # Validate score weights sum to ~1.0
    weight_keys = ["score_weight_internal", "score_weight_viral", "score_weight_keyso",
                    "score_weight_trends", "score_weight_headline"]
    if any(k in body for k in weight_keys):
        total = sum(float(body.get(k, getattr(config, k.upper(), 0))) for k in weight_keys)
        if abs(total - 1.0) > 0.05:
            return {"status": "error", "message": f"Сумма весов скоринга = {total:.2f}, должна быть 1.0 (допуск ±0.05)"}

    # Float settings
    _float_settings = {
        "llm_temperature": "LLM_TEMPERATURE",
        "score_weight_internal": "SCORE_WEIGHT_INTERNAL",
        "score_weight_viral": "SCORE_WEIGHT_VIRAL",
        "score_weight_keyso": "SCORE_WEIGHT_KEYSO",
        "score_weight_trends": "SCORE_WEIGHT_TRENDS",
        "score_weight_headline": "SCORE_WEIGHT_HEADLINE",
        "sheets_min_api_interval": "SHEETS_MIN_API_INTERVAL",
    }
    for body_key, cfg_attr in _float_settings.items():
        if body_key in body:
            try:
                new_val = round(float(body[body_key]), 4)
                old_val = getattr(config, cfg_attr, None)
                if new_val != old_val:
                    changes.append((body_key, str(old_val), str(new_val)))
                    setattr(config, cfg_attr, new_val)
            except (ValueError, TypeError):
                pass

    # New: Sheets configuration
    if "sheets_id" in body and body["sheets_id"] != config.GOOGLE_SHEETS_ID:
        changes.append(("sheets_id", config.GOOGLE_SHEETS_ID, body["sheets_id"]))
        config.GOOGLE_SHEETS_ID = body["sheets_id"]
    if "sheets_tab_ready" in body and body["sheets_tab_ready"] != getattr(config, "SHEETS_TAB_READY", "Ready"):
        changes.append(("sheets_tab_ready", getattr(config, "SHEETS_TAB_READY", "Ready"), body["sheets_tab_ready"]))
        config.SHEETS_TAB_READY = body["sheets_tab_ready"]
    if "sheets_tab_not_ready" in body and body["sheets_tab_not_ready"] != getattr(config, "SHEETS_TAB_NOT_READY", "NotReady"):
        changes.append(("sheets_tab_not_ready", getattr(config, "SHEETS_TAB_NOT_READY", "NotReady"), body["sheets_tab_not_ready"]))
        config.SHEETS_TAB_NOT_READY = body["sheets_tab_not_ready"]
    if "openai_base_url" in body and body["openai_base_url"] != getattr(config, "OPENAI_BASE_URL", ""):
        changes.append(("openai_base_url", getattr(config, "OPENAI_BASE_URL", ""), body["openai_base_url"]))
        config.OPENAI_BASE_URL = body["openai_base_url"]
    if "google_service_account_json" in body and body["google_service_account_json"]:
        changes.append(("google_service_account_json", "***", "***updated***"))
        config.GOOGLE_SERVICE_ACCOUNT_JSON = body["google_service_account_json"]
        # Force sheets client re-auth
        try:
            import storage.sheets as sheets_mod
            sheets_mod._client = None
        except Exception:
            pass

    # API keys (only save non-empty values)
    _api_keys = {
        "openai_key": ("OPENAI_API_KEY", "openai_key"),
        "keyso_key": ("KEYSO_API_KEY", "keyso_key"),
        "vk_token": ("VK_API_TOKEN", "vk_token"),
        "tg_bot_token": ("TELEGRAM_BOT_TOKEN", "tg_bot_token"),
    }
    for body_key, (cfg_attr, _) in _api_keys.items():
        val = body.get(body_key, "").strip()
        if val:
            changes.append((body_key, "***", "***updated***"))
            setattr(config, cfg_attr, val)

    # Persist ALL changed settings to DB
    from storage.database import set_app_setting
    setting_map = {
        "llm_model": "LLM_MODEL", "keyso_region": "KEYSO_REGION",
        "sheets_tab": "SHEETS_TAB", "sheets_id": "GOOGLE_SHEETS_ID",
        "sheets_tab_ready": "SHEETS_TAB_READY", "sheets_tab_not_ready": "SHEETS_TAB_NOT_READY",
        "auto_approve_threshold": "AUTO_APPROVE_THRESHOLD",
        "auto_rewrite_on_publish_now": "AUTO_REWRITE_ON_PUBLISH_NOW",
        "auto_rewrite_style": "AUTO_REWRITE_STYLE",
        "openai_base_url": "OPENAI_BASE_URL",
    }
    # Add all int/float settings to persistence map
    for body_key, cfg_attr in {**_int_settings, **_float_settings}.items():
        setting_map[body_key] = cfg_attr
    for body_key, db_key in setting_map.items():
        if body_key in body:
            set_app_setting(db_key, str(body[body_key]))
    if "google_service_account_json" in body and body["google_service_account_json"]:
        set_app_setting("GOOGLE_SERVICE_ACCOUNT_JSON", body["google_service_account_json"])
    # Persist API keys
    for body_key, (cfg_attr, _) in _api_keys.items():
        val = body.get(body_key, "").strip()
        if val:
            set_app_setting(cfg_attr, val)

    # Audit log config changes
    for setting_name, old_val, new_val in changes:
        try:
            from core.observability import log_config_change
            log_config_change(setting_name, old_val, new_val, changed_by=user)
        except Exception:
            pass

    return {"status": "ok"}


def preview_rewrite(body):
    """Test current rewrite prompt on a sample news article."""
    try:
        news_id = body.get("news_id", "")
        style = body.get("style", "news")
        custom_prompt = body.get("custom_prompt", "")

        # Get news article
        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"
            if news_id:
                cur.execute(f"SELECT title, plain_text FROM news WHERE id = {ph}", (news_id,))
            else:
                # Pick a random recent article with content
                cur.execute("SELECT title, plain_text FROM news WHERE plain_text IS NOT NULL AND LENGTH(plain_text) > 200 ORDER BY parsed_at DESC LIMIT 1")
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "Нет подходящих новостей для превью"}
            if _is_postgres():
                title, text = row[0], row[1]
            else:
                title, text = row["title"], row["plain_text"]
        finally:
            cur.close()

        if custom_prompt:
            # Use custom prompt directly
            import apis.llm as llm
            result = llm._call_llm(custom_prompt.format(
                title=title, text=text[:3000],
                style_instructions=llm.REWRITE_STYLES.get(style, llm.REWRITE_STYLES["news"])["instructions"],
                language="русский",
            ))
        else:
            from apis.llm import rewrite_news
            result = rewrite_news(title, text, style=style)

        if result:
            return {"status": "ok", "original_title": title, "result": result}
        return {"status": "error", "message": "LLM не вернул результат"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def test_llm(body):
    try:
        import config
        from openai import OpenAI
        import json as _json
        prompt = body.get("prompt", "Ответь JSON: {\"test\": \"ok\"}")
        client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        text = response.choices[0].message.content
        # Try parse JSON
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        try:
            parsed = _json.loads(cleaned)
        except Exception:
            parsed = None
        return {"status": "ok", "model": config.LLM_MODEL, "base_url": config.OPENAI_BASE_URL, "raw": text, "result": parsed}
    except Exception as e:
        return {"status": "error", "message": str(e), "type": type(e).__name__}


def test_keyso(body):
    try:
        import config
        import requests as _req
        keyword = body.get("keyword", "gta 6")
        # Raw request for debugging
        url = f"{config.KEYSO_BASE_URL}/report/simple/keyword_dashboard"
        params = {"auth-token": config.KEYSO_API_KEY, "base": config.KEYSO_REGION, "keyword": keyword}
        resp = _req.get(url, params=params, timeout=15)
        raw = resp.json()
        return {"status": "ok", "http_code": resp.status_code, "raw_response": raw}
    except Exception as e:
        return {"status": "error", "message": str(e), "type": type(e).__name__}


def test_sheets(body):
    try:
        import config
        from storage.sheets import _get_client, get_sheets_config_error
        config_error = get_sheets_config_error()
        if config_error:
            return {"status": "error", "message": config_error}
        client = _get_client()
        if not client:
            return {"status": "error", "message": "Google client init failed. Check GOOGLE_SERVICE_ACCOUNT_JSON"}
        sheet = client.open_by_key(config.GOOGLE_SHEETS_ID)
        worksheets = [ws.title for ws in sheet.worksheets()]
        tab = sheet.worksheet(config.SHEETS_TAB)
        rows = len(tab.get_all_values())
        return {"status": "ok", "sheets_id": config.GOOGLE_SHEETS_ID, "tabs": worksheets, "active_tab": config.SHEETS_TAB, "rows": rows}
    except Exception as e:
        return {"status": "error", "message": str(e), "type": type(e).__name__}


def test_parse(body):
    url = body.get("url", "")
    if not url:
        return {"status": "error", "message": "URL required"}
    try:
        from parsers.html_parser import _fetch_article
        h1, description, plain_text = _fetch_article(url)
        return {
            "status": "ok",
            "h1": h1,
            "description": description[:500],
            "plain_text": plain_text[:1000],
            "text_length": len(plain_text),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def setup_headers(body):
    try:
        from storage.sheets import setup_headers, get_sheets_config_error
        config_error = get_sheets_config_error()
        if config_error:
            return {"status": "error", "message": config_error}
        setup_headers()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def reparse_source(body):
    name = body.get("name")
    import config
    source = next((s for s in config.SOURCES if s["name"] == name), None)
    if not source:
        return {"status": "error", "message": "Source not found"}
    try:
        if source["type"] == "rss":
            from parsers.rss_parser import parse_rss_source
            count = parse_rss_source(source)
        elif source["type"] == "sitemap":
            from parsers.html_parser import parse_sitemap_source
            count = parse_sitemap_source(source)
        else:
            from parsers.html_parser import parse_html_source
            count = parse_html_source(source)
        return {"status": "ok", "new_articles": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def reparse_all(body):
    try:
        import config
        from parsers.rss_parser import parse_rss_source
        from parsers.html_parser import parse_html_source, parse_sitemap_source
        total = 0
        for source in config.SOURCES:
            try:
                if source["type"] == "rss":
                    total += parse_rss_source(source)
                elif source["type"] == "sitemap":
                    total += parse_sitemap_source(source)
                else:
                    total += parse_html_source(source)
            except Exception as e:
                logger.error("Reparse %s error: %s", source["name"], e)
        return {"status": "ok", "new_articles": total}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def heal_source(body):
    """Diagnostics and auto-healing of a problematic source."""
    name = body.get("name")
    if not name:
        return {"status": "error", "message": "name required"}
    import config
    source = next((s for s in config.SOURCES if s["name"] == name), None)
    if not source:
        return {"status": "error", "message": "Source not found"}

    steps = []
    healed = False
    new_articles = 0

    # Step 1: Reset circuit breaker for this domain
    from parsers.proxy import _circuit_breaker, _get_domain
    domain = _get_domain(source["url"])
    if domain in _circuit_breaker:
        del _circuit_breaker[domain]
        steps.append({"action": "circuit_breaker_reset", "status": "ok", "detail": f"Сброшен circuit breaker для {domain}"})
    else:
        steps.append({"action": "circuit_breaker_check", "status": "skip", "detail": "Circuit breaker не активен"})

    # Step 2: Test URL accessibility with fresh UA
    from parsers.proxy import _get_random_ua
    import requests
    test_ok = False
    test_status = 0
    test_error = ""
    try:
        headers = {"User-Agent": _get_random_ua()}
        resp = requests.get(source["url"], headers=headers, timeout=15, allow_redirects=True)
        test_status = resp.status_code
        test_ok = resp.status_code == 200
        content_len = len(resp.text)
        steps.append({"action": "url_test", "status": "ok" if test_ok else "fail",
                      "detail": f"HTTP {test_status}, {content_len} байт" + (", Cloudflare?" if resp.status_code == 403 else "")})
    except Exception as e:
        test_error = str(e)
        steps.append({"action": "url_test", "status": "fail", "detail": f"Ошибка: {test_error[:200]}"})

    # Step 3: Try alternative strategies based on source type
    if test_ok:
        # Step 3a: Try reparse with current config
        try:
            if source["type"] == "rss":
                from parsers.rss_parser import parse_rss_source
                new_articles = parse_rss_source(source)
            elif source["type"] == "sitemap":
                from parsers.html_parser import parse_sitemap_source
                new_articles = parse_sitemap_source(source)
            else:
                from parsers.html_parser import parse_html_source
                new_articles = parse_html_source(source)
            steps.append({"action": "reparse", "status": "ok", "detail": f"Получено {new_articles} новых статей"})
            if new_articles > 0:
                healed = True
        except Exception as e:
            steps.append({"action": "reparse", "status": "fail", "detail": str(e)[:200]})

        # Step 3b: If HTML source failed — try alternative selectors
        if not healed and source["type"] in ("html", "dtf"):
            alt_selectors = ["article", "div.article", ".news-item", ".post", "a[href*='news']", "a[href*='article']",
                             ".card", ".feed-item", "h2 a", "h3 a"]
            original_sel = source.get("selector", "article")
            for alt_sel in alt_selectors:
                if alt_sel == original_sel:
                    continue
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "lxml")
                    found = soup.select(alt_sel)
                    links_found = sum(1 for el in found if el.find("a", href=True) or el.name == "a")
                    if links_found >= 3:
                        steps.append({"action": "alt_selector", "status": "found",
                                      "detail": f"Селектор '{alt_sel}' нашёл {links_found} элементов (текущий: '{original_sel}')",
                                      "selector": alt_sel, "links_count": links_found})
                        break
                except Exception:
                    pass
            else:
                steps.append({"action": "alt_selector", "status": "skip", "detail": "Альтернативные селекторы не помогли"})

        # Step 3c: If RSS source got 0 articles — check feed validity
        if not healed and source["type"] == "rss":
            try:
                import feedparser
                feed = feedparser.parse(resp.content)
                n_entries = len(feed.entries)
                is_bozo = feed.bozo
                if n_entries > 0:
                    steps.append({"action": "feed_check", "status": "ok",
                                  "detail": f"RSS валидный: {n_entries} записей" + (" (bozo)" if is_bozo else "")})
                    # All entries might be existing (already parsed)
                    if new_articles == 0:
                        steps.append({"action": "feed_check", "status": "info",
                                      "detail": "Все записи уже в БД — источник работает, новых нет"})
                        healed = True
                else:
                    steps.append({"action": "feed_check", "status": "fail",
                                  "detail": f"RSS пустой или невалидный" + (f": {feed.bozo_exception}" if is_bozo else "")})
                    # Try common alternative RSS URLs
                    alt_urls = []
                    base = source["url"].rstrip("/")
                    if "/feed/" not in base:
                        alt_urls.append(base + "/feed/")
                    if "/rss" not in base:
                        alt_urls.append(base.rsplit("/", 1)[0] + "/rss/")
                        alt_urls.append(base.rsplit("/", 1)[0] + "/feed.xml")
                    for alt_url in alt_urls:
                        try:
                            alt_resp = requests.get(alt_url, headers=headers, timeout=10)
                            if alt_resp.status_code == 200:
                                alt_feed = feedparser.parse(alt_resp.content)
                                if len(alt_feed.entries) > 0:
                                    steps.append({"action": "alt_rss_url", "status": "found",
                                                  "detail": f"Найден рабочий RSS: {alt_url} ({len(alt_feed.entries)} записей)",
                                                  "url": alt_url, "entries": len(alt_feed.entries)})
                                    break
                        except Exception:
                            pass
            except Exception as e:
                steps.append({"action": "feed_check", "status": "fail", "detail": str(e)[:200]})
    else:
        # URL not accessible — suggest solutions
        if test_status == 403:
            steps.append({"action": "diagnosis", "status": "info", "detail": "403 Forbidden — вероятно Cloudflare/WAF. Рекомендация: прокси или рендер-сервис"})
        elif test_status == 404:
            steps.append({"action": "diagnosis", "status": "info", "detail": "404 Not Found — URL изменился. Проверьте актуальный адрес RSS/страницы"})
        elif test_status == 429:
            steps.append({"action": "diagnosis", "status": "info", "detail": "429 Too Many Requests — увеличьте интервал парсинга"})
        elif test_status >= 500:
            steps.append({"action": "diagnosis", "status": "info", "detail": f"Сервер {test_status} — временная проблема, повторите позже"})
        elif test_error:
            if "timeout" in test_error.lower():
                steps.append({"action": "diagnosis", "status": "info", "detail": "Таймаут — сервер не отвечает. Попробуйте прокси"})
            elif "ssl" in test_error.lower():
                steps.append({"action": "diagnosis", "status": "info", "detail": "Ошибка SSL — проблема с сертификатом"})
            else:
                steps.append({"action": "diagnosis", "status": "info", "detail": f"Сетевая ошибка: {test_error[:150]}"})

    # Step 4: Recommendation
    recommendations = []
    for step in steps:
        if step["status"] == "found" and step["action"] == "alt_selector":
            recommendations.append(f"Сменить селектор на '{step['selector']}' ({step['links_count']} элементов)")
        if step["status"] == "found" and step["action"] == "alt_rss_url":
            recommendations.append(f"Сменить URL на {step['url']}")
    if test_status == 403:
        recommendations.append("Включить прокси-ротацию (PROXY_LIST)")
        recommendations.append("Увеличить интервал парсинга")
    if test_status == 429:
        recommendations.append("Увеличить интервал парсинга до 30+ мин")
    if not healed and not recommendations:
        recommendations.append("Попробуйте парсинг вручную позже")

    return {
        "status": "ok",
        "healed": healed,
        "new_articles": new_articles,
        "steps": steps,
        "recommendations": recommendations,
        "source": name,
    }


def save_prompt_version(body):
    import uuid
    from datetime import datetime, timezone
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        name = body.get("prompt_name", "")
        content = body.get("content", "")
        notes = body.get("notes", "")
        if not name or not content:
            return {"status": "error", "message": "name and content required"}
        # Get next version
        cur.execute(f"SELECT MAX(version) as mv FROM prompt_versions WHERE prompt_name = {ph}", (name,))
        row = cur.fetchone()
        if _is_postgres():
            max_v = row[0] if row and row[0] else 0
        else:
            max_v = row["mv"] if row and row["mv"] else 0
        if max_v is None:
            max_v = 0
        version = max_v + 1
        vid = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(f"""INSERT INTO prompt_versions (id, prompt_name, version, content, is_active, created_at, notes)
            VALUES ({','.join([ph]*7)})""", (vid, name, version, content, 0, now, notes))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok", "id": vid, "version": version}
    finally:
        cur.close()


def activate_prompt_version(body):
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        vid = body.get("id", "")
        if not vid:
            return {"status": "error", "message": "id required"}
        # Get prompt name and content
        cur.execute(f"SELECT prompt_name, content FROM prompt_versions WHERE id = {ph}", (vid,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "not found"}
        if _is_postgres():
            name, content = row[0], row[1]
        else:
            name, content = row["prompt_name"], row["content"]
        # Deactivate all for this name
        cur.execute(f"UPDATE prompt_versions SET is_active = 0 WHERE prompt_name = {ph}", (name,))
        # Activate this one
        cur.execute(f"UPDATE prompt_versions SET is_active = 1 WHERE id = {ph}", (vid,))
        if not _is_postgres():
            conn.commit()
        # Apply to live prompts
        import apis.llm as llm
        prompt_map = {
            "trend_forecast": "PROMPT_TREND_FORECAST",
            "merge_analysis": "PROMPT_MERGE_ANALYSIS",
            "keyso_queries": "PROMPT_KEYSO_QUERIES",
            "rewrite": "PROMPT_REWRITE",
        }
        attr = prompt_map.get(name)
        if attr and hasattr(llm, attr):
            setattr(llm, attr, content)
            logger.info("Activated prompt version %s for %s", vid, name)
        return {"status": "ok", "prompt_name": name, "applied": bool(attr)}
    finally:
        cur.close()


def generate_digest(body):
    """Generate a digest for the given period."""
    period = body.get("period", "today")  # today, week
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        if period == "week":
            interval = "7 days"
        else:
            interval = "1 day"
        if _is_postgres():
            cur.execute(f"SELECT id, title, source, url FROM news WHERE status IN ('approved', 'processed') AND parsed_at::timestamptz > (NOW() - INTERVAL '{interval}') ORDER BY parsed_at DESC LIMIT 30")
            columns = [desc[0] for desc in cur.description]
            news_list = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            cur.execute(f"SELECT id, title, source, url FROM news WHERE status IN ('approved', 'processed') AND parsed_at > datetime('now', '-{interval}') ORDER BY parsed_at DESC LIMIT 30")
            news_list = [dict(row) for row in cur.fetchall()]

        if not news_list:
            return {"status": "ok", "digest": {"title": "Нет данных", "summary": "Нет одобренных новостей за выбранный период.", "top_news": [], "trends": []}, "news_count": 0}

        from apis.llm import _call_llm
        news_text = "\n".join(f"- [{n['source']}] {n['title']}" for n in news_list)
        period_label = 'неделю' if period == 'week' else 'день'
        prompt = f"""Ты — главный редактор крупного игрового портала. Составь профессиональный дайджест «Главное за {period_label}» из новостей ниже.

    ## Новости ({len(news_list)} шт.):
    {news_text}

    ## Правила:
    1. title — яркий заголовок дайджеста (напр. «Игровой дайджест: GTA 6, новый патч Elden Ring и скандал вокруг Ubisoft»)
    2. summary — связный текст на 4-6 предложений, охватывающий самые значимые события, не простое перечисление
    3. top_news — 3-5 самых важных новостей, одной фразой каждая (не копируй заголовки дословно, перефразируй)
    4. trends — 2-3 тенденции, которые прослеживаются в потоке новостей (напр. «Рост интереса к ретро-играм», «Волна переносов релизов»)
    5. Язык: русский

    Ответь строго JSON без markdown:
    {{
      "title": "Заголовок дайджеста",
      "summary": "Связный обзорный текст",
      "top_news": ["Ключевая новость 1", "Ключевая новость 2", "Ключевая новость 3"],
      "trends": ["Тенденция 1", "Тенденция 2"]
    }}"""
        result = _call_llm(prompt)
        if result:
            return {"status": "ok", "digest": result, "news_count": len(news_list)}
        else:
            return {"status": "error", "message": "LLM failed"}
    finally:
        cur.close()


def generate_and_save_digest(body):
    """Manual digest generation with DB save."""
    style = body.get("style", "brief")
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        if _is_postgres():
            cur.execute("""
                SELECT n.id, n.title, n.source, n.url,
                       COALESCE(a.total_score, 0) as total_score
                FROM news n
                LEFT JOIN news_analysis a ON a.news_id = n.id
                WHERE n.status IN ('approved', 'processed', 'in_review', 'ready')
                  AND n.parsed_at::timestamptz > (NOW() - INTERVAL '24 hours')
                ORDER BY COALESCE(a.total_score, 0) DESC
                LIMIT 20
            """)
            columns = [desc[0] for desc in cur.description]
            news_list = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            cur.execute("""
                SELECT n.id, n.title, n.source, n.url,
                       COALESCE(a.total_score, 0) as total_score
                FROM news n
                LEFT JOIN news_analysis a ON a.news_id = n.id
                WHERE n.status IN ('approved', 'processed', 'in_review', 'ready')
                  AND n.parsed_at > datetime('now', '-1 day')
                ORDER BY COALESCE(a.total_score, 0) DESC
                LIMIT 20
            """)
            news_list = [dict(row) for row in cur.fetchall()]

        if not news_list:
            return {"status": "ok", "digest": {"title": "Нет данных", "text": "Нет новостей за последние 24 часа.", "news_count": 0}}

        from apis.digest import generate_daily_digest
        result = generate_daily_digest(news_list, style=style)

        # Save to DB
        import uuid
        from datetime import datetime, timezone
        from storage.database import save_digest
        digest_id = str(uuid.uuid4())[:12]
        digest_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_digest(
            digest_id=digest_id,
            digest_date=digest_date,
            style=style,
            title=result.get("title", ""),
            text=result.get("text", ""),
            news_count=result.get("news_count", 0),
        )

        return {"status": "ok", "digest": result}
    except Exception as e:
        return {"status": "error", "message": str(e)[:500]}
    finally:
        cur.close()


def toggle_feature_flag(body, user="admin"):
    """Toggle a feature flag. Caller must check permissions before calling.

    Args:
        body: dict with flag_id and enabled
        user: username for audit logging
    """
    flag_id = body.get("flag_id", "")
    enabled = body.get("enabled", False)
    if not flag_id:
        return {"status": "error", "message": "flag_id required"}
    try:
        from core.feature_flags import set_flag
        set_flag(flag_id, bool(enabled), updated_by=user)
        return {"status": "ok", "flag_id": flag_id, "enabled": enabled}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_decision_trace(body):
    news_id = body.get("news_id", "")
    if not news_id:
        return {"status": "error", "message": "news_id required"}
    try:
        from core.observability import get_decision_trace
        trace = get_decision_trace(news_id)
        return {"news_id": news_id, "trace": trace}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def clear_cache(body):
    from apis.cache import clear_cache
    clear_cache()
    return {"status": "ok"}
