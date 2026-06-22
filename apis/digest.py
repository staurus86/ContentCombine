"""Daily digest generation via LLM."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DIGEST_STYLES = {
    "brief": {
        "desc": "Краткий (5-7 предложений)",
        "instructions": "Стиль: краткий дайджест. Напиши 5-7 предложений, охватывающих самые важные события. Каждое предложение — отдельная новость или тенденция. Без воды, только суть.",
    },
    "detailed": {
        "desc": "Подробный (абзац на каждую новость)",
        "instructions": "Стиль: подробный дайджест. На каждую значимую новость — отдельный абзац из 2-3 предложений с контекстом и деталями. Расположи по важности.",
    },
    "telegram": {
        "desc": "Telegram (короткий)",
        "instructions": "Стиль: пост для Telegram-канала. Используй эмодзи в начале каждого пункта. Короткие строки, между ними пустые строки. Максимум 500 символов. В конце — 3-5 хештегов.",
    },
}

PROMPT_DIGEST = """Ты — главный редактор крупного игрового портала. Составь дайджест игровых новостей за день.

## Стиль
{style_instructions}

## Новости ({news_count} шт.):
{titles_and_scores}

## Правила:
1. title — яркий заголовок дайджеста
2. text — основной текст дайджеста в заданном стиле
3. Охвати только самые значимые новости (не все подряд)
4. Если есть несколько новостей на одну тему — объедини в один пункт
5. Язык: русский

Ответь строго JSON без markdown:
{{
  "title": "Заголовок дайджеста",
  "text": "Полный текст дайджеста"
}}"""


def generate_daily_digest(news_list: list[dict], style: str = "brief") -> dict:
    """Генерирует дайджест из списка новостей через LLM.

    Args:
        news_list: список словарей с ключами title, source, и опционально total_score
        style: brief | detailed | telegram

    Returns:
        {"title": "...", "text": "...", "news_count": N}
    """
    if not news_list:
        return {"title": "Нет данных", "text": "Нет новостей за выбранный период.", "news_count": 0}

    style_data = DIGEST_STYLES.get(style, DIGEST_STYLES["brief"])

    titles_and_scores = "\n".join(
        f"- [{n.get('source', '?')}] {n.get('title', '?')} (скор: {n.get('total_score', '?')})"
        for n in news_list
    )

    prompt = PROMPT_DIGEST.format(
        style_instructions=style_data["instructions"],
        news_count=len(news_list),
        titles_and_scores=titles_and_scores,
    )

    from apis.llm import _call_llm
    result = _call_llm(prompt)

    if result:
        return {
            "title": result.get("title", "Дайджест"),
            "text": result.get("text", ""),
            "news_count": len(news_list),
        }

    logger.error("Digest LLM call failed")
    return {"title": "Ошибка", "text": "Не удалось сгенерировать дайджест (LLM недоступен).", "news_count": 0}
