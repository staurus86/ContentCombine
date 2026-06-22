"""Queue API — standalone functions extracted from web.py."""

import logging
import threading

from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


def _start_sheets_worker(task_ids: list[str]):
    """Start background worker for specific sheets task ids."""
    if not task_ids:
        return

    def _process_sheets_queue(ids: list[str]):
        import json as _json
        from datetime import datetime, timezone
        from api.sheets_export import export_news_by_policy

        conn2 = get_connection()
        cur2 = conn2.cursor()
        try:
            ph = "%s" if _is_postgres() else "?"
            for tid in ids:
                cur2.execute(f"SELECT * FROM task_queue WHERE id = {ph}", (tid,))
                row = cur2.fetchone()
                if not row:
                    continue
                if _is_postgres():
                    cols = [d[0] for d in cur2.description]
                    task = dict(zip(cols, row))
                else:
                    task = dict(row)
                if task["status"] != "pending":
                    continue

                nid = task["news_id"]
                _now = datetime.now(timezone.utc).isoformat()
                cur2.execute(
                    f"UPDATE task_queue SET status = 'processing', updated_at = {ph} WHERE id = {ph}",
                    (_now, tid),
                )
                if not _is_postgres():
                    conn2.commit()
                try:
                    cur2.execute(f"SELECT * FROM news WHERE id = {ph}", (nid,))
                    news_row = cur2.fetchone()
                    if not news_row:
                        raise Exception("news not found")
                    if _is_postgres():
                        cols = [d[0] for d in cur2.description]
                        news = dict(zip(cols, news_row))
                    else:
                        news = dict(news_row)

                    cur2.execute(f"SELECT * FROM news_analysis WHERE news_id = {ph}", (nid,))
                    arow = cur2.fetchone()
                    if arow:
                        if _is_postgres():
                            cols = [d[0] for d in cur2.description]
                            analysis = dict(zip(cols, arow))
                        else:
                            analysis = dict(arow)
                    else:
                        analysis = {
                            "bigrams": "[]",
                            "trends_data": "{}",
                            "keyso_data": "{}",
                            "llm_recommendation": "",
                            "llm_trend_forecast": "",
                            "llm_merged_with": "",
                        }

                    result = export_news_by_policy(cur2, news, analysis)
                    sheet_row = result.get("row")
                    _now2 = datetime.now(timezone.utc).isoformat()
                    if sheet_row and sheet_row > 0:
                        res_data = _json.dumps({"row": sheet_row, "destination": result.get("destination")}, ensure_ascii=False)
                        cur2.execute(
                            f"UPDATE task_queue SET status = 'done', result = {ph}, updated_at = {ph} WHERE id = {ph}",
                            (res_data, _now2, tid),
                        )
                    elif sheet_row == -1:
                        cur2.execute(
                            f"UPDATE task_queue SET status = 'skipped', result = 'duplicate', updated_at = {ph} WHERE id = {ph}",
                            (_now2, tid),
                        )
                    elif result.get("destination") == "skip":
                        cur2.execute(
                            f"UPDATE task_queue SET status = 'skipped', result = {ph}, updated_at = {ph} WHERE id = {ph}",
                            (result.get("reason", "Not eligible for export"), _now2, tid),
                        )
                    else:
                        cur2.execute(
                            f"UPDATE task_queue SET status = 'error', result = 'no row', updated_at = {ph} WHERE id = {ph}",
                            (_now2, tid),
                        )
                    if not _is_postgres():
                        conn2.commit()
                except Exception as e:
                    logger.warning("Queue sheets error %s: %s", tid, e)
                    _now2 = datetime.now(timezone.utc).isoformat()
                    cur2.execute(
                        f"UPDATE task_queue SET status = 'error', result = {ph}, updated_at = {ph} WHERE id = {ph}",
                        (str(e), _now2, tid),
                    )
                    if not _is_postgres():
                        conn2.commit()
        finally:
            cur2.close()

    threading.Thread(target=_process_sheets_queue, args=(list(task_ids),), daemon=True).start()


def get_queue(status_filter: str = "", task_type_filter: str = ""):
    """Return tasks from queue (up to 200), with optional filters."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        conditions = []
        params = []
        if status_filter:
            conditions.append(f"status = {ph}")
            params.append(status_filter)
        if task_type_filter:
            conditions.append(f"task_type = {ph}")
            params.append(task_type_filter)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        q = f"SELECT * FROM task_queue {where} ORDER BY created_at DESC LIMIT 200"
        cur.execute(q, params)
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
        return {"status": "ok", "tasks": rows}

    finally:
        cur.close()


def cancel_queue_task(body):
    """Cancel a single pending task by id."""
    task_id = body.get("task_id")
    if not task_id:
        return {"status": "error", "message": "task_id required"}
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        cur.execute(f"UPDATE task_queue SET status = 'cancelled', updated_at = {ph} WHERE id = {ph} AND status = 'pending'", (now, task_id))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok"}

    finally:
        cur.close()


def cancel_all_queue(body):
    """Cancel all pending tasks, optionally filtered by task_type."""
    task_type = body.get("task_type", "")
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        if task_type:
            cur.execute(f"UPDATE task_queue SET status = 'cancelled', updated_at = {ph} WHERE status = 'pending' AND task_type = {ph}", (now, task_type))
        else:
            cur.execute(f"UPDATE task_queue SET status = 'cancelled', updated_at = {ph} WHERE status = 'pending'", (now,))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok"}

    finally:
        cur.close()


def clear_done_queue(body):
    """Delete all completed/cancelled/error tasks from queue."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM task_queue WHERE status IN ('done', 'cancelled', 'skipped', 'error')")
        if not _is_postgres():
            conn.commit()
        return {"status": "ok"}

    finally:
        cur.close()


def retry_queue_tasks(body):
    """Retry selected tasks (error/cancelled/skipped/done -> pending) and re-run them."""
    task_ids = body.get("task_ids", [])
    if not task_ids:
        return {"status": "error", "message": "task_ids required"}
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        for tid in task_ids:
            cur.execute(f"UPDATE task_queue SET status = 'pending', result = '', updated_at = {ph} WHERE id = {ph} AND status IN ('error', 'cancelled', 'skipped', 'done')", (now, tid))
            count += cur.rowcount if hasattr(cur, 'rowcount') else 1
        if not _is_postgres():
            conn.commit()

        # Re-run pending tasks in background
        cur.execute("SELECT id, task_type, news_id, news_title, style FROM task_queue WHERE status = 'pending' ORDER BY created_at")
        if _is_postgres():
            cols = [d[0] for d in cur.description]
            pending = [dict(zip(cols, r)) for r in cur.fetchall()]
        else:
            pending = [dict(r) for r in cur.fetchall()]

        if pending:
            no_llm_tasks = [t for t in pending if t["task_type"] == "no_llm"]
            full_auto_tasks = [t for t in pending if t["task_type"] == "full_auto"]
            sheets_tasks = [t for t in pending if t["task_type"] == "sheets"]

            if no_llm_tasks:
                nids = [t["news_id"] for t in no_llm_tasks]
                tids = [t["id"] for t in no_llm_tasks]
                from scheduler import run_no_llm_pipeline
                threading.Thread(target=run_no_llm_pipeline, args=(nids, tids), daemon=True).start()
            if full_auto_tasks:
                nids = [t["news_id"] for t in full_auto_tasks]
                tids = [t["id"] for t in full_auto_tasks]
                from scheduler import run_full_auto_pipeline
                threading.Thread(target=run_full_auto_pipeline, args=(nids, tids), daemon=True).start()
            if sheets_tasks:
                _start_sheets_worker([t["id"] for t in sheets_tasks])

        return {"status": "ok", "retried": count}
    finally:
        cur.close()


def queue_batch_rewrite(body):
    """Queue news for rewrite and process in background thread."""
    news_ids = body.get("news_ids", [])
    style = body.get("style", "news")
    language = body.get("language", "русский")
    if not news_ids:
        return {"status": "error", "message": "news_ids required"}

    import uuid
    from datetime import datetime, timezone
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        now = datetime.now(timezone.utc).isoformat()
        created = []

        for nid in news_ids:
            cur.execute(f"SELECT title FROM news WHERE id = {ph}", (nid,))
            row = cur.fetchone()
            title = ""
            if row:
                title = row[0] if _is_postgres() else row["title"]
            tid = str(uuid.uuid4())[:12]
            cur.execute(f"""INSERT INTO task_queue (id, task_type, news_id, news_title, style, status, created_at, updated_at)
                VALUES ({','.join([ph]*8)})""",
                (tid, "rewrite", nid, title[:200], style, "pending", now, now))
            created.append(tid)

        if not _is_postgres():
            conn.commit()

        # Process in background thread
        def _process_rewrite_queue():
            import json as _json
            from apis.llm import rewrite_news
            conn2 = get_connection()
            cur2 = conn2.cursor()
            try:
                for tid in created:
                    cur2.execute(f"SELECT * FROM task_queue WHERE id = {ph}", (tid,))
                    if _is_postgres():
                        cols = [d[0] for d in cur2.description]
                        task = dict(zip(cols, cur2.fetchone()))
                    else:
                        task = dict(cur2.fetchone())
                    if task["status"] != "pending":
                        continue
                    nid = task["news_id"]
                    _now = datetime.now(timezone.utc).isoformat()
                    cur2.execute(f"UPDATE task_queue SET status = 'processing', updated_at = {ph} WHERE id = {ph}", (_now, tid))
                    if not _is_postgres():
                        conn2.commit()
                    try:
                        cur2.execute(f"SELECT id, title, plain_text, description, url, source FROM news WHERE id = {ph}", (nid,))
                        row = cur2.fetchone()
                        if not row:
                            raise Exception("news not found")
                        if _is_postgres():
                            cols = [d[0] for d in cur2.description]
                            news = dict(zip(cols, row))
                        else:
                            news = dict(row)
                        ntitle = news.get("title", "")
                        ntext = news.get("plain_text", "") or news.get("description", "")
                        result = rewrite_news(ntitle, ntext, style, language)
                        if not result:
                            raise Exception("LLM failed")
                        aid = str(uuid.uuid4())[:12]
                        tags = _json.dumps(result.get("tags", []), ensure_ascii=False)
                        cur2.execute(f"""INSERT INTO articles (id, news_id, title, text, seo_title, seo_description, tags,
                            style, language, original_title, original_text, source_url, status, created_at, updated_at)
                            VALUES ({','.join([ph]*15)})""",
                            (aid, nid, result.get("title", ""), result.get("text", ""),
                             result.get("seo_title", ""), result.get("seo_description", ""), tags,
                             style, language, ntitle, ntext[:5000],
                             news.get("url", ""), "draft", _now, _now))
                        if not _is_postgres():
                            conn2.commit()

                        # Export to Sheets/Ready
                        try:
                            from storage.sheets import write_ready_row
                            # Fetch full news + analysis for Sheets
                            cur2.execute(f"SELECT * FROM news WHERE id = {ph}", (nid,))
                            if _is_postgres():
                                nc = [d[0] for d in cur2.description]
                                full_news = dict(zip(nc, cur2.fetchone()))
                            else:
                                full_news = dict(cur2.fetchone())
                            cur2.execute(f"SELECT * FROM news_analysis WHERE news_id = {ph}", (nid,))
                            arow = cur2.fetchone()
                            analysis = None
                            if arow:
                                if _is_postgres():
                                    ac = [d[0] for d in cur2.description]
                                    analysis = dict(zip(ac, arow))
                                else:
                                    analysis = dict(arow)
                            write_ready_row(full_news, analysis, result)
                        except Exception as sheets_err:
                            logger.warning("Sheets Ready export failed for %s: %s", nid, sheets_err)

                        _now2 = datetime.now(timezone.utc).isoformat()
                        res_data = _json.dumps({"article_id": aid, "title": result.get("title", "")}, ensure_ascii=False)
                        cur2.execute(f"UPDATE task_queue SET status = 'done', result = {ph}, updated_at = {ph} WHERE id = {ph}", (res_data, _now2, tid))
                        if not _is_postgres():
                            conn2.commit()
                    except Exception as e:
                        logger.warning("Queue rewrite error %s: %s", tid, e)
                        _now2 = datetime.now(timezone.utc).isoformat()
                        cur2.execute(f"UPDATE task_queue SET status = 'error', result = {ph}, updated_at = {ph} WHERE id = {ph}", (str(e), _now2, tid))
                        if not _is_postgres():
                            conn2.commit()

            finally:
                cur2.close()
        t = threading.Thread(target=_process_rewrite_queue, daemon=True)
        t.start()
        return {"status": "ok", "queued": len(created), "task_ids": created}

    finally:
        cur.close()


def queue_sheets_export(body):
    """Queue news for Sheets export and process in background thread."""
    news_ids = body.get("news_ids", [])
    if not news_ids:
        return {"status": "error", "message": "news_ids required"}
    from storage.sheets import get_sheets_config_error
    config_error = get_sheets_config_error()
    if config_error:
        return {"status": "error", "message": config_error}

    import uuid
    from datetime import datetime, timezone
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        now = datetime.now(timezone.utc).isoformat()
        created = []

        for nid in news_ids:
            cur.execute(f"SELECT title FROM news WHERE id = {ph}", (nid,))
            row = cur.fetchone()
            title = ""
            if row:
                title = row[0] if _is_postgres() else row["title"]
            tid = str(uuid.uuid4())[:12]
            cur.execute(f"""INSERT INTO task_queue (id, task_type, news_id, news_title, style, status, created_at, updated_at)
                VALUES ({','.join([ph]*8)})""",
                (tid, "sheets", nid, title[:200], "", "pending", now, now))
            created.append(tid)

        if not _is_postgres():
            conn.commit()

        _start_sheets_worker(created)
        return {"status": "ok", "queued": len(created), "task_ids": created}

    finally:
        cur.close()
