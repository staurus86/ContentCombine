"""Extracted article-related methods from web.py."""
import json
import logging
import uuid
from datetime import datetime, timezone

from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ph():
    return "%s" if _is_postgres() else "?"


def _row_to_dict(cur, row):
    if _is_postgres():
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    return dict(row)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def get_articles(limit: int = 500):
    """Return articles ordered by updated_at DESC, with limit."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        cur.execute(f"SELECT * FROM articles WHERE COALESCE(is_deleted, 0) = 0 ORDER BY updated_at DESC LIMIT {ph}", (limit,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()


def save_article(body):
    aid = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = _ph()
        tags = json.dumps(body.get("tags", []), ensure_ascii=False)
        cur.execute(f"""INSERT INTO articles (id, news_id, title, text, seo_title, seo_description,
            feed_description, tags, style, language, original_title, original_text, source_url, status, created_at, updated_at)
            VALUES ({','.join([ph]*16)})""",
            (aid, body.get("news_id", ""), body.get("title", ""), body.get("text", ""),
             body.get("seo_title", ""), body.get("seo_description", ""),
             body.get("feed_description", "")[:500], tags,
             body.get("style", ""), body.get("language", "русский"),
             body.get("original_title", ""), body.get("original_text", ""),
             body.get("source_url", ""), "draft", now, now))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok", "id": aid}
    finally:
        cur.close()


def update_article(body, changed_by="admin"):
    aid = body.get("id")
    if not aid:
        return {"status": "error", "message": "id required"}

    # Save version snapshot before update (Phase 2)
    try:
        save_article_version(aid, body, change_type="manual", changed_by=changed_by)
    except Exception:
        pass

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = _ph()
        tags = json.dumps(body.get("tags", []), ensure_ascii=False)
        cur.execute(f"""UPDATE articles SET title={ph}, text={ph}, seo_title={ph},
            seo_description={ph}, tags={ph}, status={ph}, updated_at={ph} WHERE id={ph}""",
            (body.get("title", ""), body.get("text", ""), body.get("seo_title", ""),
             body.get("seo_description", ""), tags, body.get("status", "draft"), now, aid))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()


def delete_article(body):
    """Soft-delete article."""
    aid = body.get("id")
    if not aid:
        return {"status": "error", "message": "id required"}
    conn = get_connection()
    cur = conn.cursor()
    try:
        p = _ph()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(f"UPDATE articles SET is_deleted=1, deleted_at={p} WHERE id = {p}", (now, aid))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()


def restore_article(body):
    """Restore soft-deleted article."""
    aid = body.get("id")
    if not aid:
        return {"status": "error", "message": "id required"}
    conn = get_connection()
    cur = conn.cursor()
    try:
        p = _ph()
        cur.execute(f"UPDATE articles SET is_deleted=0, deleted_at=NULL WHERE id = {p}", (aid,))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()


def article_detail(body):
    aid = body.get("id")
    if not aid:
        return {"status": "error", "message": "id required"}
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = _ph()
        cur.execute(f"SELECT * FROM articles WHERE id = {ph}", (aid,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "Not found"}
        article = _row_to_dict(cur, row)
        return {"status": "ok", "article": article}
    finally:
        cur.close()


def schedule_article(body):
    """Запланировать публикацию статьи на указанное время."""
    aid = body.get("article_id") or body.get("id")
    scheduled_at = body.get("scheduled_at")
    if not aid:
        return {"status": "error", "message": "article_id required"}
    if not scheduled_at:
        return {"status": "error", "message": "scheduled_at required"}
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = _ph()
        cur.execute(f"UPDATE articles SET scheduled_at={ph}, status='scheduled', updated_at={ph} WHERE id={ph}",
                    (scheduled_at, now, aid))
        if not _is_postgres():
            conn.commit()
        return {"status": "ok", "scheduled_at": scheduled_at}
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Rewrite / Improve / LLM
# ---------------------------------------------------------------------------

def rewrite_article(body):
    """Переписать существующую статью в другом стиле."""
    aid = body.get("id")
    style = body.get("style", "news")
    language = body.get("language", "русский")
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = _ph()
        cur.execute(f"SELECT title, text, original_title, original_text FROM articles WHERE id = {ph}", (aid,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "Article not found"}
        article = _row_to_dict(cur, row)
        # Use original text for rewriting to avoid degradation
        src_title = article.get("original_title") or article.get("title", "")
        src_text = article.get("original_text") or article.get("text", "")
        from apis.llm import rewrite_news
        result = rewrite_news(src_title, src_text, style, language)
        if result:
            return {"status": "ok", "result": result}
        else:
            return {"status": "error", "message": "LLM returned no result"}
    finally:
        cur.close()


def improve_article(body):
    """Улучшить текст статьи через LLM (грамматика, стиль, SEO)."""
    aid = body.get("id")
    action = body.get("action", "improve")  # improve, expand, shorten, fix_grammar, add_seo
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = _ph()
        cur.execute(f"SELECT title, text FROM articles WHERE id = {ph}", (aid,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "Article not found"}
        article = _row_to_dict(cur, row)

        actions_map = {
            "improve": "Улучши текст: исправь стилистические ошибки, сделай более профессиональным, сохрани факты.",
            "expand": "Расширь текст: добавь подробностей, контекста, аналитики. Увеличь объём в 1.5-2 раза, не добавляя вымышленных фактов.",
            "shorten": "Сократи текст в 2 раза, оставив только ключевые факты. Убери воду и повторы.",
            "fix_grammar": "Исправь все грамматические, пунктуационные и стилистические ошибки. Не меняй смысл и структуру.",
            "add_seo": "Добавь SEO-оптимизацию: включи ключевые слова естественно, добавь подзаголовки (## H2), улучши мета-описание.",
            "make_engaging": "Сделай текст более вовлекающим: добавь интригу, живые примеры, вопросы к читателю. Сохрани факты.",
        }
        instruction = actions_map.get(action, actions_map["improve"])

        from apis.llm import _call_llm
        prompt = f"""Ты — профессиональный редактор игровых новостей.

    Задача: {instruction}

    Заголовок: {article['title']}
    Текст: {article['text'][:4000]}

    Верни строго JSON (без markdown):
    {{
      "title": "обновлённый заголовок",
      "text": "обновлённый текст",
      "seo_title": "SEO title до 60 символов",
      "seo_description": "meta description до 155 символов",
      "changes_summary": "что было изменено (1-2 предложения)"
    }}"""
        result = _call_llm(prompt)
        if result:
            return {"status": "ok", "result": result}
        else:
            return {"status": "error", "message": "LLM returned no result"}
    finally:
        cur.close()


def rewrite_news_handler(body):
    """Rewrite a news item (not an article) via LLM."""
    news_id = body.get("news_id")
    style = body.get("style", "news")
    language = body.get("language", "русский")
    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            ph = _ph()
            cur.execute(f"SELECT title, plain_text, description FROM news WHERE id = {ph}", (news_id,))
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "News not found"}
            news = _row_to_dict(cur, row)
            title = news.get("title", "")
            text = news.get("plain_text", "") or news.get("description", "")
            from apis.llm import rewrite_news
            result = rewrite_news(title, text, style, language)
            if result:
                return {"status": "ok", "result": result, "original_title": title}
            else:
                return {"status": "error", "message": "LLM returned no result"}
        finally:
            cur.close()
    except Exception as e:
        return {"status": "error", "message": str(e)}


def batch_rewrite(body):
    """Батч-переписка новостей: создаёт статьи из списка news_ids."""
    news_ids = body.get("news_ids", [])
    style = body.get("style", "news")
    language = body.get("language", "русский")
    if not news_ids:
        return {"status": "error", "message": "news_ids required"}
    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = _ph()
        from apis.llm import rewrite_news

        results = []
        for nid in news_ids:
            try:
                cur.execute(f"SELECT id, title, plain_text, description, url, source FROM news WHERE id = {ph}", (nid,))
                row = cur.fetchone()
                if not row:
                    results.append({"news_id": nid, "ok": False, "error": "not found"})
                    continue
                news = _row_to_dict(cur, row)
                title = news.get("title", "")
                text = news.get("plain_text", "") or news.get("description", "")
                result = rewrite_news(title, text, style, language)
                if not result:
                    results.append({"news_id": nid, "ok": False, "error": "LLM failed"})
                    continue
                # Save as article
                aid = str(uuid.uuid4())[:12]
                now = datetime.now(timezone.utc).isoformat()
                tags = json.dumps(result.get("tags", []), ensure_ascii=False)
                cur.execute(f"""INSERT INTO articles (id, news_id, title, text, seo_title, seo_description,
                    feed_description, tags, style, language, original_title, original_text, source_url, status, created_at, updated_at)
                    VALUES ({','.join([ph]*16)})""",
                    (aid, nid, result.get("title", ""), result.get("text", ""),
                     result.get("seo_title", ""), result.get("seo_description", ""),
                     result.get("feed_description", "")[:500], tags,
                     style, language, title, text[:5000],
                     news.get("url", ""), "draft", now, now))
                if not _is_postgres():
                    conn.commit()
                results.append({"news_id": nid, "ok": True, "article_id": aid, "title": result.get("title", "")})
            except Exception as e:
                logger.warning("Batch rewrite error for %s: %s", nid, e)
                results.append({"news_id": nid, "ok": False, "error": str(e)})

        ok_count = sum(1 for r in results if r.get("ok"))
        return {"status": "ok", "total": len(news_ids), "success": ok_count,
                "failed": len(news_ids) - ok_count, "results": results}
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Versions (Phase 2)
# ---------------------------------------------------------------------------

def get_article_versions(body):
    """Get version history for an article."""
    article_id = body.get("article_id", "")
    if not article_id:
        return {"status": "error", "message": "article_id required"}
    conn = get_connection()
    cur = conn.cursor()
    ph = _ph()
    try:
        cur.execute(f"""
            SELECT * FROM article_versions
            WHERE article_id = {ph}
            ORDER BY version DESC
        """, (article_id,))
        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        else:
            rows = [dict(r) for r in cur.fetchall()]
        return {"versions": rows}
    except Exception as e:
        return {"status": "error", "message": str(e), "versions": []}
    finally:
        cur.close()


def save_article_version(article_id, article_data, change_type="manual", changed_by="system"):
    """Save a version snapshot before modification (internal helper)."""
    try:
        from core.feature_flags import is_enabled
        if not is_enabled("content_versions_v1"):
            return
    except Exception:
        return

    conn = get_connection()
    cur = conn.cursor()
    ph = _ph()
    try:
        # Get current max version
        cur.execute(f"SELECT COALESCE(MAX(version), 0) FROM article_versions WHERE article_id = {ph}", (article_id,))
        max_ver = cur.fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        ver_id = uuid.uuid4().hex[:12]

        cur.execute(f"""
            INSERT INTO article_versions (id, article_id, version, title, text, seo_title, seo_description, tags, change_type, changed_by, created_at)
            VALUES ({','.join([ph]*11)})
        """, (ver_id, article_id, max_ver + 1,
              article_data.get("title", "")[:500],
              article_data.get("text", "")[:5000],
              article_data.get("seo_title", "")[:500],
              article_data.get("seo_description", "")[:1000],
              json.dumps(article_data.get("tags", []), ensure_ascii=False),
              change_type, changed_by, now))
        if not _is_postgres():
            conn.commit()
    except Exception as e:
        logger.debug("Failed to save article version: %s", e)
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Multi-output / Regenerate (Phase 2)
# ---------------------------------------------------------------------------

def generate_multi_output(body):
    """Generate multiple output formats from one article."""
    article_id = body.get("article_id", "")
    formats = body.get("formats", ["social", "short"])
    if not article_id:
        return {"status": "error", "message": "article_id required"}

    conn = get_connection()
    cur = conn.cursor()
    ph = _ph()
    try:
        cur.execute(f"SELECT title, text FROM articles WHERE id = {ph}", (article_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "article not found"}
        if _is_postgres():
            title, text = row[0], row[1]
        else:
            title, text = row["title"], row["text"]

        from apis.llm import rewrite_news
        results = {}
        for fmt in formats[:3]:  # Max 3 formats at once
            result = rewrite_news(title=title, text=text, style=fmt, language="русский")
            if result:
                results[fmt] = result

        return {"article_id": article_id, "outputs": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        cur.close()


def export_articles_xlsx(query_params) -> bytes:
    """Export rewritten articles as XLSX in CMS-ready format."""
    import io
    import json as _json
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    conn = get_connection()
    cur = conn.cursor()
    try:
        ph = "%s" if _is_postgres() else "?"
        status_filter = query_params.get("status", [None])[0]
        style_filter = query_params.get("style", [None])[0]
        limit = int(query_params.get("limit", ["500"])[0])

        conditions = ["COALESCE(a.is_deleted, 0) = 0"]
        params = []
        if status_filter:
            conditions.append(f"a.status = {ph}")
            params.append(status_filter)
        if style_filter:
            conditions.append(f"a.style = {ph}")
            params.append(style_filter)

        where = "WHERE " + " AND ".join(conditions)

        cur.execute(f"""
            SELECT a.title, a.feed_description, a.seo_title, a.seo_description,
                   a.text, a.tags, a.style, a.source_url, a.status, a.created_at,
                   a.original_title
            FROM articles a
            {where}
            ORDER BY a.created_at DESC
            LIMIT {ph}
        """, params + [limit])

        if _is_postgres():
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        else:
            rows = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "CMS Export"

    headers = [
        "Заголовок", "Описание для ленты", "SEO заголовок",
        "SEO описание", "Текст полной новости", "Теги",
        "Стиль", "URL оригинала", "Статус", "Дата создания"
    ]

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="17BF63", end_color="17BF63", fill_type="solid")

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for row_idx, a in enumerate(rows, 2):
        tags = a.get("tags", "")
        if isinstance(tags, str):
            try:
                tags = _json.loads(tags)
            except Exception:
                tags = []
        tags_str = ", ".join(t.get("label", t.get("id", str(t))) if isinstance(t, dict) else str(t) for t in tags) if isinstance(tags, list) else str(tags)

        values = [
            a.get("title", ""),
            a.get("feed_description", ""),
            a.get("seo_title", ""),
            a.get("seo_description", ""),
            a.get("text", ""),
            tags_str,
            a.get("style", ""),
            a.get("source_url", ""),
            a.get("status", ""),
            (a.get("created_at") or "")[:16].replace("T", " "),
        ]
        for col_idx, val in enumerate(values, 1):
            ws.cell(row=row_idx, column=col_idx, value=val)

    col_widths = [50, 40, 50, 60, 80, 30, 10, 40, 10, 16]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def regenerate_field(body):
    """Regenerate a single field (title, seo_title, seo_description, tags) via LLM."""
    article_id = body.get("article_id", "")
    field = body.get("field", "")
    if not article_id or field not in ("title", "seo_title", "seo_description", "tags"):
        return {"status": "error", "message": "article_id and valid field required"}

    conn = get_connection()
    cur = conn.cursor()
    ph = _ph()
    try:
        cur.execute(f"SELECT title, text FROM articles WHERE id = {ph}", (article_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "article not found"}
        if _is_postgres():
            title, text = row[0], row[1]
        else:
            title, text = row["title"], row["text"]

        from apis.llm import _call_llm
        prompts = {
            "title": f"Придумай новый заголовок для игровой новости. Текст:\n{text[:1500]}\n\nОтветь JSON: {{\"title\": \"новый заголовок\"}}",
            "seo_title": f"Придумай SEO-заголовок до 60 символов для:\n{title}\n\nОтветь JSON: {{\"seo_title\": \"SEO заголовок\"}}",
            "seo_description": f"Придумай meta description до 155 символов для:\n{title}\n{text[:500]}\n\nОтветь JSON: {{\"seo_description\": \"описание\"}}",
            "tags": f"Предложи 5 тегов для игровой новости:\n{title}\n\nОтветь JSON: {{\"tags\": [\"тег1\", \"тег2\", \"тег3\", \"тег4\", \"тег5\"]}}",
        }

        result = _call_llm(prompts[field])
        if result and field in result:
            return {"field": field, "value": result[field]}
        else:
            return {"status": "error", "message": "LLM returned no result"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        cur.close()
