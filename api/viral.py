"""Viral API — standalone functions extracted from web.py."""

import logging

from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def get_viral(query_params):
    """Analyse virality: run all news through viral_score + sentiment + momentum.

    Args:
        query_params: dict-like from parse_qs (values are lists), e.g. {"limit": ["200"], "level": ["high"]}
    """
    from checks.viral_score import viral_score, VIRAL_TRIGGERS, get_calendar_boost
    from checks.sentiment import analyze_sentiment
    from checks.tags import auto_tag
    from apis.cache import cache_get, cache_set, cache_key

    limit = int(query_params.get("limit", [200])[0])
    level_filter = query_params.get("level", [None])[0]
    category_filter = query_params.get("category", [None])[0]
    sentiment_filter = query_params.get("sentiment", [None])[0]
    source_filter = query_params.get("source", [None])[0]
    date_from = query_params.get("date_from", [None])[0]
    date_to = query_params.get("date_to", [None])[0]
    trigger_filter = query_params.get("trigger", [None])[0]
    min_score = int(query_params.get("min_score", [0])[0])

    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"

        conditions = []
        params = []
        if source_filter:
            conditions.append(f"n.source = {ph}")
            params.append(source_filter)
        if date_from:
            conditions.append(f"n.parsed_at >= {ph}")
            params.append(date_from)
        if date_to:
            conditions.append(f"n.parsed_at <= {ph}")
            params.append(date_to + "T23:59:59")
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        cur.execute(f"""
            SELECT n.id, n.source, n.title, n.url, n.description, n.plain_text,
                   n.published_at, n.parsed_at, n.status
            FROM news n {where}
            ORDER BY n.parsed_at DESC LIMIT {ph}
        """, params + [limit])

        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]

        items = []
        stats = {"total": 0, "high": 0, "medium": 0, "low": 0, "none": 0}
        trigger_counts = {}
        category_counts = {}
        sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0}
        source_scores = {}

        # Category mapping from trigger_id prefix
        CATEGORY_MAP = {
            "scandal": "Скандалы", "leak": "Утечки", "shadow": "Shadow Drops",
            "bad": "Плохие релизы", "ai": "AI", "major_event": "Ивенты",
            "event": "Ивенты", "money": "Деньги", "culture": "Культура",
            "person": "Персоны", "speed": "Скорость",
            "sequel": "Базовые", "free_content": "Базовые", "delay": "Базовые",
            "canceled": "Базовые", "award": "Базовые", "next_gen": "Базовые",
            "big_update": "Базовые", "release_date": "Базовые",
            "trailer": "Базовые", "record": "Базовые", "digest": "Базовые",
        }

        for row in rows:
            ck = cache_key("viral_tab", row["id"])
            cached = cache_get(ck)
            if cached:
                vr = cached["viral"]
                sent = cached["sentiment"]
                tags = cached["tags"]
            else:
                vr = viral_score(row)
                sent = analyze_sentiment(row)
                tags = auto_tag(row)
                cache_set(ck, {"viral": vr, "sentiment": sent, "tags": tags}, ttl=3600)

            # Determine categories of triggers
            trigger_categories = set()
            for t in vr["triggers"]:
                tid = t["id"]
                prefix = tid.split("_")[0]
                cat = CATEGORY_MAP.get(tid, CATEGORY_MAP.get(prefix, "Прочее"))
                trigger_categories.add(cat)

            # Apply filters
            if level_filter and vr["level"] != level_filter:
                continue
            if min_score and vr["score"] < min_score:
                continue
            if sentiment_filter and sent["label"] != sentiment_filter:
                continue
            if trigger_filter:
                if not any(t["id"] == trigger_filter for t in vr["triggers"]):
                    continue
            if category_filter:
                if category_filter not in trigger_categories:
                    continue

            item = {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"],
                "parsed_at": row["parsed_at"],
                "status": row["status"],
                "viral_score": vr["score"],
                "viral_level": vr["level"],
                "triggers": vr["triggers"],
                "sentiment": sent["label"],
                "sentiment_score": sent["score"],
                "tags": [{"id": t["id"], "label": t["label"]} for t in tags[:3]],
            }
            items.append(item)

            # Aggregate stats
            stats["total"] += 1
            stats[vr["level"]] = stats.get(vr["level"], 0) + 1
            sentiment_counts[sent["label"]] = sentiment_counts.get(sent["label"], 0) + 1
            for t in vr["triggers"]:
                trigger_counts[t["label"]] = trigger_counts.get(t["label"], 0) + 1
                prefix = t["id"].split("_")[0]
                cat = CATEGORY_MAP.get(t["id"], CATEGORY_MAP.get(prefix, "Прочее"))
                category_counts[cat] = category_counts.get(cat, 0) + 1
            src = row["source"]
            if src not in source_scores:
                source_scores[src] = {"total": 0, "sum": 0}
            source_scores[src]["total"] += 1
            source_scores[src]["sum"] += vr["score"]

        # Sort by viral_score desc
        items.sort(key=lambda x: x["viral_score"], reverse=True)

        # Top triggers sorted
        top_triggers = sorted(trigger_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        # Top categories sorted
        top_categories = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)

        # Source avg scores
        source_avg = []
        for src, data in source_scores.items():
            source_avg.append({"source": src, "avg": round(data["sum"] / data["total"], 1), "count": data["total"]})
        source_avg.sort(key=lambda x: x["avg"], reverse=True)

        # Calendar event
        cal_boost, cal_event = get_calendar_boost()

        # Available triggers for filter
        all_triggers = [{"id": k, "label": v["label"], "category": CATEGORY_MAP.get(k, CATEGORY_MAP.get(k.split("_")[0], "Прочее"))} for k, v in VIRAL_TRIGGERS.items()]

        return {
            "items": items,
            "stats": stats,
            "sentiment": sentiment_counts,
            "top_triggers": top_triggers,
            "top_categories": top_categories,
            "source_avg": source_avg[:15],
            "calendar": {"boost": cal_boost, "event": cal_event},
            "all_triggers": all_triggers,
        }

    finally:
        cur.close()


def get_viral_triggers():
    """Return all viral triggers (defaults + custom from DB)."""
    from checks.viral_score import VIRAL_TRIGGERS
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Load DB overrides
        db_triggers = {}
        try:
            cur.execute("SELECT trigger_id, label, weight, keywords, is_active, is_custom FROM viral_triggers_config")
            for row in cur.fetchall():
                if _is_postgres():
                    tid, label, weight, kw_json, active, custom = row
                else:
                    tid, label, weight, kw_json, active, custom = row["trigger_id"], row["label"], row["weight"], row["keywords"], row["is_active"], row["is_custom"]
                import json as _j
                kws = _j.loads(kw_json) if isinstance(kw_json, str) else (kw_json or [])
                db_triggers[tid] = {"label": label, "weight": weight, "keywords": kws, "is_active": bool(active), "is_custom": bool(custom)}
        except Exception:
            pass

        result = []
        # Default triggers
        for tid, tdata in VIRAL_TRIGGERS.items():
            if tid in db_triggers:
                dt = db_triggers[tid]
                result.append({"id": tid, "label": dt["label"], "weight": dt["weight"], "keywords": dt["keywords"], "is_active": dt["is_active"], "is_custom": False, "modified": True})
            else:
                result.append({"id": tid, "label": tdata["label"], "weight": tdata["weight"], "keywords": tdata["keywords"], "is_active": True, "is_custom": False, "modified": False})

        # Custom-only triggers (not in defaults)
        for tid, dt in db_triggers.items():
            if tid not in VIRAL_TRIGGERS:
                result.append({"id": tid, "label": dt["label"], "weight": dt["weight"], "keywords": dt["keywords"], "is_active": dt["is_active"], "is_custom": True, "modified": False})

        result.sort(key=lambda x: (-x["weight"], x["label"]))
        return {"triggers": result, "total": len(result)}
    finally:
        cur.close()


def save_viral_trigger(body):
    """Save or update a viral trigger."""
    trigger_id = body.get("trigger_id", "").strip()
    label = body.get("label", "").strip()
    weight = int(body.get("weight", 0))
    keywords = body.get("keywords", [])
    is_active = bool(body.get("is_active", True))
    is_custom = bool(body.get("is_custom", False))

    if not trigger_id or not label:
        return {"status": "error", "message": "trigger_id and label required"}

    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    import json as _j
    from datetime import datetime, timezone
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        now = datetime.now(timezone.utc).isoformat()
        kw_json = _j.dumps(keywords, ensure_ascii=False)

        if _is_postgres():
            cur.execute(f"""
                INSERT INTO viral_triggers_config (trigger_id, label, weight, keywords, is_active, is_custom, updated_at)
                VALUES ({','.join([ph]*7)})
                ON CONFLICT (trigger_id) DO UPDATE SET label={ph}, weight={ph}, keywords={ph}, is_active={ph}, updated_at={ph}
            """, (trigger_id, label, weight, kw_json, 1 if is_active else 0, 1 if is_custom else 0, now,
                  label, weight, kw_json, 1 if is_active else 0, now))
        else:
            cur.execute(f"INSERT OR REPLACE INTO viral_triggers_config (trigger_id, label, weight, keywords, is_active, is_custom, updated_at) VALUES ({','.join([ph]*7)})",
                        (trigger_id, label, weight, kw_json, 1 if is_active else 0, 1 if is_custom else 0, now))
            conn.commit()

        # Rebuild index
        from checks.viral_score import reload_viral_triggers
        reload_viral_triggers()

        return {"status": "ok", "trigger_id": trigger_id}
    finally:
        cur.close()


def delete_viral_trigger(body):
    """Delete a custom trigger or reset a modified default."""
    trigger_id = body.get("trigger_id", "")
    if not trigger_id:
        return {"status": "error", "message": "trigger_id required"}
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        cur.execute(f"DELETE FROM viral_triggers_config WHERE trigger_id = {ph}", (trigger_id,))
        if not _is_postgres():
            conn.commit()
        from checks.viral_score import reload_viral_triggers
        reload_viral_triggers()
        return {"status": "ok"}
    finally:
        cur.close()
