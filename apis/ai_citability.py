"""Измерение реальной AI-цитируемости: кого цитируют AI-поисковики по SEO/GEO-запросам.

Sprint 5 (quality-journalism), GEO killer-feature. Раз в неделю прогоняем набор
запросов (config.CITABILITY_QUERIES) через движки с НАСТОЯЩИМИ цитатами и копим,
какие домены цитируются. Итог — поток «кого AI цитирует на этой неделе» + движение
доменов неделя к неделе.

Движки:
- Perplexity API (если задан PERPLEXITY_API_KEY): поле citations в ответе.
- OpenAI search-модели через гейтвей (gpt-4o-mini-search-preview и др. из
  CITABILITY_SEARCH_MODELS): web_search с url_citation аннотациями.

Принцип честности: обычные LLM без веб-поиска НЕ используются — их «цитаты»
это галлюцинации из training data, не реальная цитируемость. Нет ни одного
рабочего движка → скан честно возвращает no_engine и ничего не пишет.
"""

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import config
from storage.database import get_connection, _is_postgres

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+")


def _domain(url: str) -> str:
    try:
        d = (urlparse(url).netloc or "").lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Движки. Каждый возвращает list[{"url","title"}] РЕАЛЬНЫХ цитат или None,
# если движок недоступен (None = «не работает», [] = «работает, цитат нет»).
# ---------------------------------------------------------------------------

def _ask_perplexity(query: str) -> list[dict] | None:
    if not config.PERPLEXITY_API_KEY:
        return None
    try:
        import requests
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {config.PERPLEXITY_API_KEY}"},
            json={"model": "sonar", "messages": [{"role": "user", "content": query}]},
            timeout=45,
        )
        if resp.status_code != 200:
            logger.warning("Perplexity HTTP %d: %s", resp.status_code, resp.text[:120])
            return None
        data = resp.json()
        cites = data.get("citations") or []
        # новые версии API кладут в search_results [{url,title}]
        results = data.get("search_results") or []
        if results:
            return [{"url": r.get("url", ""), "title": r.get("title", "")} for r in results if r.get("url")]
        return [{"url": u, "title": ""} for u in cites if isinstance(u, str)]
    except Exception as e:
        logger.warning("Perplexity engine failed: %s", e)
        return None


def _ask_openai_search(query: str, model: str) -> list[dict] | None:
    """Search-модель через существующий OpenAI-клиент (гейтвей artemox).
    Цитаты берём из url_citation-аннотаций; фолбэк — url в тексте ответа."""
    try:
        from apis.llm import get_client
        resp = get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": query}],
            timeout=60,
        )
        msg = resp.choices[0].message
        out, seen = [], set()
        for ann in (getattr(msg, "annotations", None) or []):
            uc = getattr(ann, "url_citation", None)
            url = getattr(uc, "url", "") if uc else ""
            if url and url not in seen:
                seen.add(url)
                out.append({"url": url, "title": getattr(uc, "title", "") or ""})
        if not out:
            for url in _URL_RE.findall(msg.content or ""):
                if url not in seen:
                    seen.add(url)
                    out.append({"url": url, "title": ""})
        return out
    except Exception as e:
        logger.info("Search model %s unavailable: %s", model, str(e)[:150])
        return None


def _detect_engines() -> list[tuple[str, callable]]:
    """Список рабочих движков, определяется пробой на первом запросе скана."""
    engines = []
    if config.PERPLEXITY_API_KEY:
        engines.append(("perplexity", _ask_perplexity))
    for m in config.CITABILITY_SEARCH_MODELS:
        engines.append((f"openai:{m}", lambda q, _m=m: _ask_openai_search(q, _m)))
    return engines


# ---------------------------------------------------------------------------
# Скан и отчёт
# ---------------------------------------------------------------------------

def run_citability_scan(limit_queries: int = 0) -> dict:
    """Прогоняет запросы через рабочие движки, пишет цитаты в ai_citations.

    limit_queries > 0 — ограничить число запросов (для smoke-теста).
    Возвращает сводку: движки, запросов прогнано, цитат записано.
    """
    queries = config.CITABILITY_QUERIES
    if limit_queries > 0:
        queries = queries[:limit_queries]
    if not queries:
        return {"status": "error", "message": "no queries configured"}

    candidates = _detect_engines()
    if not candidates:
        return {"status": "no_engine",
                "message": "Нет движков с реальными цитатами: задай PERPLEXITY_API_KEY "
                           "или CITABILITY_SEARCH_MODELS, доступные на гейтвее."}

    # Проба движков на первом запросе: отбрасываем нерабочие один раз,
    # а не мучаем каждый запрос всеми фолбэками.
    working = []
    for name, fn in candidates:
        probe = fn(queries[0])
        if probe is not None:
            working.append((name, fn, {queries[0]: probe}))
            logger.info("Citability engine OK: %s (%d citations on probe)", name, len(probe))
    if not working:
        return {"status": "no_engine",
                "message": "Ни один движок не вернул цитаты (search-модели не проксируются "
                           "гейтвеем, Perplexity-ключа нет). Фейковые цитаты не пишем."}

    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    written = 0
    try:
        # идемпотентность: повторный скан в тот же день перезаписывает свой срез
        cur.execute(f"DELETE FROM ai_citations WHERE scan_date = {ph}", (scan_date,))
        for name, fn, cache in working:
            for q in queries:
                cites = cache.get(q) if q in cache else fn(q)
                if not cites:
                    continue
                for rank, c in enumerate(cites[:10], 1):
                    url = (c.get("url") or "").strip()
                    dom = _domain(url)
                    if not dom:
                        continue
                    cur.execute(
                        f"INSERT INTO ai_citations (scan_date, engine, query, domain, url, title, rank, created_at) "
                        f"VALUES ({','.join([ph]*8)})",
                        (scan_date, name, q, dom, url, (c.get("title") or "")[:300], rank, now))
                    written += 1
        if not _is_postgres():
            conn.commit()
    finally:
        cur.close()

    logger.info("Citability scan done: engines=%s queries=%d citations=%d",
                [w[0] for w in working], len(queries), written)
    return {"status": "ok", "scan_date": scan_date,
            "engines": [w[0] for w in working],
            "queries": len(queries), "citations_written": written}


def get_citability_report() -> dict:
    """Отчёт: топ цитируемых доменов последнего скана + движение против прошлого."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if _is_postgres() else "?"
    try:
        try:
            cur.execute("SELECT DISTINCT scan_date FROM ai_citations ORDER BY scan_date DESC LIMIT 2")
        except Exception:
            # таблицы ещё нет (init_db не отработал) — пустой отчёт, не 500
            return {"status": "empty", "message": "Сканов ещё не было — запусти /api/citability/scan.",
                    "queries": [{"query": q, "citations": 0, "domains": 0} for q in config.CITABILITY_QUERIES]}
        dates = [r[0] for r in cur.fetchall()]
        if not dates:
            return {"status": "empty", "message": "Сканов ещё не было — запусти /api/citability/scan.",
                    "queries": [{"query": q, "citations": 0, "domains": 0} for q in config.CITABILITY_QUERIES]}
        latest = dates[0]
        prior = dates[1] if len(dates) > 1 else None

        def _top(scan_date):
            cur.execute(f"""
                SELECT domain, COUNT(*) AS n, COUNT(DISTINCT query) AS nq
                FROM ai_citations WHERE scan_date = {ph}
                GROUP BY domain ORDER BY n DESC LIMIT 30
            """, (scan_date,))
            return {r[0]: {"citations": r[1], "queries": r[2]} for r in cur.fetchall()}

        top = _top(latest)
        prev = _top(prior) if prior else {}
        movers = []
        for dom, cur_stats in top.items():
            was = prev.get(dom, {}).get("citations", 0)
            movers.append({"domain": dom, **cur_stats, "delta": cur_stats["citations"] - was,
                           "new": dom not in prev if prior else False})

        cur.execute(f"""
            SELECT engine, COUNT(*), COUNT(DISTINCT query) FROM ai_citations
            WHERE scan_date = {ph} GROUP BY engine
        """, (latest,))
        engines = [{"engine": r[0], "citations": r[1], "queries": r[2]} for r in cur.fetchall()]

        # Список запросов со статистикой последнего скана — в порядке конфига,
        # включая «молчащие» (0 цитат): видно, какие вопросы AI не активируют.
        cur.execute(f"""
            SELECT query, COUNT(*), COUNT(DISTINCT domain) FROM ai_citations
            WHERE scan_date = {ph} GROUP BY query
        """, (latest,))
        qstats = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        queries = [{"query": q,
                    "citations": qstats.get(q, (0, 0))[0],
                    "domains": qstats.get(q, (0, 0))[1]} for q in config.CITABILITY_QUERIES]

        return {"status": "ok", "scan_date": latest, "prior_scan": prior,
                "engines": engines, "top_domains": movers, "queries": queries}
    finally:
        cur.close()
