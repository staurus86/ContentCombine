"""Daily digest generation via LLM.

Detailed digest returns STRUCTURED items: the model references each source by its
number, and we render the real source links ourselves (no URL hallucination).
Brief and telegram styles return free-form text.
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_RU_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]


def _ru_date_today() -> str:
    """Today's date in Russian, Moscow time (UTC+3)."""
    d = datetime.now(timezone.utc) + timedelta(hours=3)
    return f"{d.day} {_RU_MONTHS[d.month - 1]} {d.year}"

# Anti-AI-slop guardrails applied to every digest prompt. Mirrors the user's
# Writing Standard: active voice, concrete facts, no signature AI phrasing.
ANTI_SLOP = """## Как писать
- Активный залог, сильный глагол. Подлежащее — кто действует.
- Конкретика: имена, числа, продукты, даты. Не «эксперты считают» — кто именно.
- Каждое слово несёт смысл. Режь воду и вводные.
- Своя позиция: что это значит для SEO-специалиста, а не пересказ заголовка.

## Запрещено
- Канцелярит и штампы ИИ: «важно отметить», «следует учитывать», «в современном мире»,
  «на сегодняшний день», «ключевую роль играет», «таким образом», «давайте рассмотрим»,
  «стоит отметить», «не секрет, что», «в эпоху цифровизации».
- Конструкция «Это не X. Это Y.» — пиши прямое утверждение.
- Вводные «В этом дайджесте…», «Разберём…», «Итак,» — сразу к сути.
- Наречия-усилители без нужды («крайне», «поистине», «несомненно»).
- Восторженные эпитеты ради красоты («революционный», «прорывной», «бесшовный»)."""


def _format_sources(news_list: list[dict]) -> str:
    """Numbered source list passed to the model (with date for freshness)."""
    lines = []
    for i, n in enumerate(news_list, 1):
        date = (n.get("published_at") or n.get("parsed_at") or "")[:10]
        src = n.get("source", "?")
        title = n.get("title", "?")
        lines.append(f"{i}. [{src}] {title}" + (f" ({date})" if date else ""))
    return "\n".join(lines)


PROMPT_DETAILED = """Ты — выпускающий редактор отраслевого издания про SEO, AI и digital-маркетинг.
Составь подробный дайджест свежих новостей для профессиональной аудитории.

{anti_slop}

## Новости ({news_count} шт.) — ссылайся на них по номеру:
{numbered}

## Задача
1. Отбери только значимые новости. Проходные анонсы и перепечатки пропусти.
2. Несколько новостей об одном событии — объедини в один пункт, перечислив все номера-источники.
3. На каждый пункт: ёмкий заголовок и 2-3 предложения с сутью и тем, что это меняет на практике.
4. Расположи пункты по важности.
5. В поле sources укажи номера новостей, на которых основан пункт (одно или несколько).

Верни строго JSON без markdown:
{{
  "title": "Заголовок дайджеста — по главной теме дня, конкретно",
  "items": [
    {{"headline": "Заголовок пункта", "summary": "2-3 предложения по сути", "sources": [1, 4]}}
  ]
}}"""


PROMPT_BRIEF = """Ты — выпускающий редактор издания про SEO, AI и digital-маркетинг.
Составь краткий дайджест свежих новостей.

{anti_slop}

## Новости ({news_count} шт.):
{numbered}

## Задача
Напиши 5-7 предложений сплошным текстом, каждое — отдельное значимое событие или тенденция.
Только самое важное, по убыванию значимости. Без перечня источников.

Верни строго JSON без markdown:
{{"title": "Заголовок дайджеста", "text": "Связный текст из 5-7 предложений"}}"""


PROMPT_TELEGRAM = """Ты — редактор Telegram-канала про SEO, AI и digital-маркетинг.
Составь пост-дайджест для подписчиков.

{anti_slop}

## Новости ({news_count} шт.) — ссылайся по номеру:
{numbered}

## Формат
- 4-6 пунктов, каждый с эмодзи в начале и пустой строкой между ними.
- Одна-две живые фразы на пункт, без канцелярита.
- В sources укажи номера источников пункта.
- Заверши 3-5 хештегами по темам (в поле hashtags).

Верни строго JSON без markdown:
{{
  "title": "Заголовок поста",
  "items": [{{"headline": "🔍 Суть пункта одной фразой", "summary": "", "sources": [2]}}],
  "hashtags": ["#SEO", "#AI"]
}}"""


def _render_links(news_list, sources, md=True):
    """Build the source link suffix for one item from referenced indices."""
    seen, links = set(), []
    for idx in sources or []:
        if not isinstance(idx, int) or idx < 1 or idx > len(news_list) or idx in seen:
            continue
        seen.add(idx)
        n = news_list[idx - 1]
        src = (n.get("source") or "Источник").strip()
        url = (n.get("url") or "").strip()
        if not url:
            continue
        links.append(f"[{src}]({url})" if md else f"{src} {url}")
    return links


def _source_suffix(news_list, sources) -> str:
    """Compact «(Источник)» links per item. One source → (Источник); many → numbered."""
    urls, seen = [], set()
    for idx in sources or []:
        if not isinstance(idx, int) or idx < 1 or idx > len(news_list) or idx in seen:
            continue
        seen.add(idx)
        url = (news_list[idx - 1].get("url") or "").strip()
        if url:
            urls.append(url)
    if not urls:
        return ""
    if len(urls) == 1:
        return f"([Источник]({urls[0]}))"
    return " ".join(f"([Источник {i}]({u}))" for i, u in enumerate(urls, 1))


def _render_detailed(result, news_list) -> str:
    """Markdown: bold headline, paragraph, «(Источник)» link(s) after each item."""
    blocks = []
    for it in result.get("items", []):
        head = (it.get("headline") or "").strip()
        body = (it.get("summary") or "").strip()
        suffix = _source_suffix(news_list, it.get("sources"))
        block = f"**{head}**" if head else ""
        if body:
            block += ("\n" if block else "") + body
        if suffix:
            block += (" " if block else "") + suffix
        if block.strip():
            blocks.append(block.strip())
    return "\n\n".join(blocks)


def _render_telegram(result, news_list) -> str:
    """Telegram post: emoji line + source link per item, hashtags at the end."""
    lines = []
    for it in result.get("items", []):
        head = (it.get("headline") or "").strip()
        body = (it.get("summary") or "").strip()
        links = _render_links(news_list, it.get("sources"), md=True)
        line = head
        if body:
            line += " — " + body
        if links:
            line += "\n" + " · ".join(links)
        if line.strip():
            lines.append(line.strip())
    text = "\n\n".join(lines)
    tags = result.get("hashtags") or []
    if tags:
        text += "\n\n" + " ".join(t if str(t).startswith("#") else "#" + str(t) for t in tags)
    return text


def _top_tags(news_list, n=3) -> list[str]:
    """Top-N most frequent tag labels across the digest news (самые обсуждаемые)."""
    import json as _json
    from collections import Counter
    counts = Counter()
    for item in news_list:
        td = item.get("tags_data")
        if isinstance(td, str):
            try:
                td = _json.loads(td or "[]")
            except Exception:
                td = []
        for t in td or []:
            label = (t.get("label") or t.get("id") or "").strip() if isinstance(t, dict) else str(t).strip()
            if len(label) >= 2:
                counts[label] += 1
    return [lbl for lbl, _ in counts.most_common(n)]


def generate_daily_digest(news_list: list[dict], style: str = "brief") -> dict:
    """Генерирует дайджест из списка новостей через LLM.

    Args:
        news_list: словари с title, source, url, published_at, опц. total_score
        style: brief | detailed | telegram

    Returns:
        {"title": "...", "text": "...", "news_count": N}
    """
    if not news_list:
        return {"title": "Нет данных", "text": "Нет свежих новостей за выбранный период.", "news_count": 0}

    numbered = _format_sources(news_list)
    common = {"anti_slop": ANTI_SLOP, "news_count": len(news_list), "numbered": numbered}

    if style == "detailed":
        prompt = PROMPT_DETAILED.format(**common)
    elif style == "telegram":
        prompt = PROMPT_TELEGRAM.format(**common)
    else:
        prompt = PROMPT_BRIEF.format(**common)

    from apis.llm import _call_llm
    result = _call_llm(prompt)

    if not result:
        logger.error("Digest LLM call failed")
        return {"title": "Ошибка", "text": "Не удалось сгенерировать дайджест (LLM недоступен).", "news_count": 0}

    if style == "detailed":
        text = _render_detailed(result, news_list)
        if text:
            text = f"📅 {_ru_date_today()}\n\n" + text
            tags = _top_tags(news_list, 3)
            if tags:
                text += "\n\n**Главные темы дня:** " + " · ".join(tags)
    elif style == "telegram":
        text = _render_telegram(result, news_list)
    else:
        text = result.get("text", "")

    return {
        "title": result.get("title", "Дайджест"),
        "text": text or "Не удалось собрать пункты дайджеста.",
        "news_count": len(news_list),
    }
