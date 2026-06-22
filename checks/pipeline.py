import logging
from checks.deduplication import tfidf_similarity, build_groups
from checks.quality import check_quality
from checks.relevance import check_relevance
from checks.freshness import check_freshness
from checks.viral_score import viral_score
from checks.tags import auto_tag
from checks.sentiment import analyze_sentiment
from checks.momentum import get_momentum, invalidate_cache
from checks.ner import extract_entities
from checks.headline_score import headline_score
from checks.source_weight import get_source_weight
from checks.feedback import get_feedback_adjustments
from nlp.game_entities import find_entities
from storage.database import get_connection, _is_postgres, update_news_status, save_check_results

logger = logging.getLogger(__name__)


def _check_single(news: dict) -> dict:
    """Проверяет одну новость. Возвращает результат без изменения статуса."""
    result = {
        "id": news["id"],
        "title": news.get("title", ""),
        "source": news.get("source", ""),
        "url": news.get("url", ""),
        "published_at": news.get("published_at", ""),
        "checks": {},
    }

    # Quality — первая проверка, определяет early exit
    result["checks"]["quality"] = check_quality(news)
    q_score = result["checks"]["quality"]["score"]

    # Early exit: если quality < 20, пропускаем тяжёлые проверки
    if q_score < 20:
        result["checks"]["relevance"] = {"score": 0, "pass": False, "issues": ["skipped: low quality"]}
        result["checks"]["freshness"] = {"score": 0, "pass": False, "status": "unknown", "age_hours": -1}
        result["checks"]["viral"] = {"score": 0, "pass": False, "level": "none", "triggers": []}
        result["tags"] = []
        result["sentiment"] = {"label": "neutral", "score": 0}
        result["momentum"] = {"score": 0, "level": "none"}
        result["entities"] = {"studios": [], "games": [], "platforms": [], "numbers": [], "events": [], "total_entities": 0}
        result["headline"] = {"score": 0}
        result["source_weight"] = 1.0
        result["game_entities"] = []
        result["overall_pass"] = False
        result["total_score"] = max(0, q_score // 5)  # scale 0-100 quality to ~0-20 total
        result["feedback_adjustment"] = 0.0
        result["status"] = news.get("status", "new")
        result["early_exit"] = True
        return result

    # Compute text once, reuse for all checks — fallback to description if plain_text empty
    plain = news.get("plain_text", "") or news.get("description", "") or ""
    text = news.get("title", "") + " " + plain

    # Game entities — computed once, passed to viral_score
    game_ents = find_entities(text)

    # Основные проверки
    result["checks"]["relevance"] = check_relevance(news)
    result["checks"]["freshness"] = check_freshness(news)
    result["checks"]["viral"] = viral_score(news, precomputed_entities=game_ents)

    # Дополнительные анализы
    result["tags"] = auto_tag(news)
    result["sentiment"] = analyze_sentiment(news)
    result["momentum"] = get_momentum(news)
    result["entities"] = extract_entities(news)
    result["headline"] = headline_score(news)
    result["source_weight"] = get_source_weight(news.get("source", ""))

    result["game_entities"] = game_ents

    all_pass = all(c["pass"] for c in result["checks"].values())
    total_score = sum(c["score"] for c in result["checks"].values()) // 4

    # Momentum бустит score
    momentum_bonus = result["momentum"]["score"] // 5
    total_score = min(100, total_score + momentum_bonus)

    # Source weight multiplier
    sw = result["source_weight"]
    total_score = min(100, int(total_score * sw))

    # Headline bonus
    headline_bonus = max(0, (result["headline"]["score"] - 50)) // 10
    total_score = min(100, total_score + headline_bonus)

    # Feedback adjustment — apply learned weights from editor decisions
    feedback_adj = 0.0
    try:
        fb = get_feedback_adjustments()
        source = news.get("source", "")
        # Source-based adjustment: adjustment is in -0.2..+0.2 range, scale to points
        if source in fb["sources"]:
            feedback_adj += fb["sources"][source]["adjustment"] * 50  # -10..+10

        # Tag-based adjustment: average adjustment across matching tags
        detected_tags = [t.get("tag", t) if isinstance(t, dict) else t for t in result.get("tags", [])]
        tag_adjs = []
        for tag in detected_tags:
            if tag in fb["tags"]:
                tag_adjs.append(fb["tags"][tag]["adjustment"] * 50)
        if tag_adjs:
            feedback_adj += sum(tag_adjs) / len(tag_adjs)

        # Cap to ±10 points
        feedback_adj = max(-10.0, min(10.0, feedback_adj))
    except Exception as e:
        logger.debug("Feedback adjustment error: %s", e)
        feedback_adj = 0.0

    total_score = max(0, min(100, int(total_score + feedback_adj)))
    result["feedback_adjustment"] = round(feedback_adj, 2)

    result["overall_pass"] = all_pass
    result["total_score"] = total_score
    result["status"] = news.get("status", "new")

    # Score breakdown for explainability
    result["score_breakdown"] = {
        "quality": result["checks"]["quality"]["score"],
        "relevance": result["checks"]["relevance"]["score"],
        "freshness": result["checks"]["freshness"]["score"],
        "viral": result["checks"]["viral"]["score"],
        "momentum_bonus": momentum_bonus,
        "headline_bonus": headline_bonus,
        "source_weight": sw,
        "feedback_adj": round(feedback_adj, 2),
        "base_avg": sum(c["score"] for c in result["checks"].values()) // 4,
        "final_total": total_score,
    }

    return result


def run_review_pipeline(news_list: list[dict], update_status: bool = True) -> dict:
    """Запускает все этапы проверки для списка новостей.

    Args:
        news_list: список новостей для проверки
        update_status: если True — обновляет статусы в БД (in_review/duplicate)
    """
    # Chunk large batches to avoid O(n²) dedup explosion
    CHUNK_SIZE = 100
    if len(news_list) > CHUNK_SIZE:
        all_results = []
        all_groups = []
        for start in range(0, len(news_list), CHUNK_SIZE):
            chunk = news_list[start:start + CHUNK_SIZE]
            chunk_result = run_review_pipeline(chunk, update_status=update_status)
            all_results.extend(chunk_result.get("results", []))
            all_groups.extend(chunk_result.get("groups", []))
        return {"results": all_results, "groups": all_groups}

    try:
        results = [_check_single(news) for news in news_list]
    finally:
        # Invalidate momentum cache after batch completes
        invalidate_cache()

    # Dedup across batch (TF-IDF + entity overlap)
    titles = [r["title"] for r in results]
    texts = [r["title"] + " " + (news_list[i].get("plain_text", "") if i < len(news_list) else "")
             for i, r in enumerate(results)]
    pairs = tfidf_similarity(titles, texts)
    groups = build_groups(results, pairs)

    # Mark duplicates
    for group in groups:
        for idx in group.get("duplicate_indices", []):
            if idx < len(results):
                results[idx]["overall_pass"] = False
                results[idx]["is_duplicate"] = True

        for member in group["members"]:
            member["dedup_status"] = group["status"]

    # Decision trace helper (best-effort, non-blocking)
    def _trace(news_id, step, decision, reason="", details=None, s_before=0, s_after=0):
        try:
            from core.observability import log_decision
            log_decision(news_id, step, decision, reason, details, s_before, s_after)
        except Exception:
            pass

    # Save check results in DB (always) + update statuses (optional)
    AUTO_REJECT_SCORE = 15
    for r in results:
        if update_status:
            if r.get("is_duplicate"):
                update_news_status(r["id"], "duplicate")
                _trace(r["id"], "review_pipeline", "duplicate",
                       "Дубликат обнаружен по TF-IDF/entity overlap")
            elif r.get("total_score", 0) < AUTO_REJECT_SCORE:
                update_news_status(r["id"], "rejected")
                r["auto_rejected"] = True
                _trace(r["id"], "review_pipeline", "auto_rejected",
                       f"total_score={r.get('total_score',0)} < {AUTO_REJECT_SCORE}",
                       s_after=r.get("total_score", 0))
            else:
                update_news_status(r["id"], "in_review")
                _trace(r["id"], "review_pipeline", "in_review",
                       f"total_score={r.get('total_score',0)}, готово к модерации",
                       s_after=r.get("total_score", 0))
        try:
            save_check_results(
                r["id"], r["checks"],
                sentiment=r.get("sentiment"),
                tags=r.get("tags"),
                momentum=r.get("momentum"),
                headline=r.get("headline"),
                total_score=r.get("total_score", 0),
                entities=r.get("game_entities"),
                score_breakdown=r.get("score_breakdown"),
            )
        except Exception as e:
            logger.warning("Failed to save check results for %s: %s", r["id"], e)

    return {"results": results, "groups": groups}


def approve_for_enrichment(news_ids: list[str]):
    """Одобряет новости и ставит в очередь на обогащение."""
    for nid in news_ids:
        update_news_status(nid, "approved")
    logger.info("Approved %d news for enrichment", len(news_ids))
