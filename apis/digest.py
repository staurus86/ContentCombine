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


def _call_llm_retry(prompt: str, attempts: int = 2, pause: float = 2.0):
    """Call the LLM with up to N attempts and a short pause between — smooths over
    transient flaps. Kept to 2 attempts so a slow/degraded gateway doesn't make the
    request hang for minutes."""
    import time
    from apis.llm import _call_llm
    for i in range(attempts):
        result = _call_llm(prompt)
        if result:
            return result
        if i < attempts - 1:
            logger.warning("Digest LLM attempt %d/%d failed — retrying in %.0fs", i + 1, attempts, pause)
            time.sleep(pause)
    return None

# Anti-AI-slop guardrails applied to every digest prompt. Mirrors the user's
# Writing Standard: active voice, concrete facts, no signature AI phrasing.
ANTI_SLOP = """## Язык
- Пиши на русском языке, даже если источники на английском — переводи заголовки и суть.

## Как писать
- Активный залог, сильный глагол. Подлежащее — кто действует.
- Действует человек или компания, не абстракция. «Google подтвердил», «Anthropic выкатил» —
  не «данные показывают», «исследование утверждает». Всегда называй, кто именно сделал.
- Конкретика: имена, числа, продукты, даты. Не «эксперты считают» — кто именно.
- Показывай цифрой, не оценкой: «трафик упал на 40%», а не «заметно просел».
- Каждое слово несёт смысл. Режь воду и вводные.
- Своя позиция: что это значит для SEO-специалиста, а не пересказ заголовка.

## Запрещено
- Канцелярит и штампы ИИ: «важно отметить», «следует учитывать», «в современном мире»,
  «на сегодняшний день», «ключевую роль играет», «таким образом», «давайте рассмотрим»,
  «стоит отметить», «не секрет, что», «в эпоху цифровизации», «подчёркивает», «демонстрирует».
- «является», «представляет собой» — ставь тире или глагол: «X — инструмент», не «X является инструментом».
- Расщепление сказуемого: «производит анализ» → «анализирует», «ведёт борьбу» → «борется».
- Конструкция «Это не X. Это Y.» — пиши прямое утверждение.
- Вводные «В этом дайджесте…», «Разберём…», «Итак,» — сразу к сути.
- Наречия-усилители без нужды («крайне», «поистине», «несомненно»).
- Восторженные эпитеты ради красоты («революционный», «прорывной», «бесшовный»).

## Формат
- Не подгоняй под «три пункта» ради симметрии — сколько реально есть, столько и пиши.
- Тире не вместо запятой. Жирным — только настоящий акцент, не каждая фраза."""


def _format_sources(news_list: list[dict]) -> str:
    """Numbered source list passed to the model (with date and score hints)."""
    lines = []
    for i, n in enumerate(news_list, 1):
        date = (n.get("published_at") or n.get("parsed_at") or "")[:10]
        src = n.get("source", "?")
        title = n.get("title", "?")
        sc = n.get("total_score", 0)
        v = n.get("viral_score", 0)
        hints = []
        if date:
            hints.append(date)
        if sc:
            hints.append(f"скор {sc}")
        if v:
            hints.append(f"вирал {v}")
        lines.append(f"{i}. [{src}] {title}" + (f" ({', '.join(hints)})" if hints else ""))
    return "\n".join(lines)


def _log_llm_selection(result: dict, news_list: list[dict], max_items: int, tag: str):
    """Логирует, какие кандидаты выбрала LLM и какие топовые по скору пропустила.

    Список кандидатов приходит отсортированным по скору DESC, поэтому индексы
    1..max_items — это топ. Без лога отклонения LLM от скоринга невозможно
    анализировать."""
    try:
        items = result.get("items") or []
        chosen = sorted({idx for it in items for idx in (it.get("sources") or [])
                         if isinstance(idx, int) and 1 <= idx <= len(news_list)})
        top = set(range(1, min(max_items, len(news_list)) + 1))
        skipped_top = sorted(top - set(chosen))
        logger.info("Digest LLM selection [%s]: chose %s of %d candidates%s",
                    tag, chosen, len(news_list),
                    f"; skipped top-score {skipped_top}" if skipped_top else "")
    except Exception:
        logger.debug("Digest selection logging failed", exc_info=True)


PROMPT_DETAILED = """Ты — выпускающий редактор отраслевого издания про SEO, AI и digital-маркетинг.
Составь подробный дайджест свежих новостей для профессиональной аудитории.

{anti_slop}

## Новости ({news_count} шт.) — ссылайся на них по номеру:
{numbered}

## Задача
1. Отбери только САМОЕ значимое — строго не больше {max_items} пунктов. Проходные анонсы и перепечатки пропусти.
2. Несколько новостей об одном событии — объедини в один пункт, перечислив все номера-источники.
3. На каждый пункт: ёмкий заголовок и 1-2 предложения с сутью и тем, что это меняет на практике. Без воды.
4. Расположи пункты по важности.
5. В поле sources укажи номера новостей, на которых основан пункт (одно или несколько).
6. Дайджест должен умещаться в одно сообщение Telegram — будь лаконичен.

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
    """Compact source links per item, labelled with the channel/source NAME:
    «(DrMax SEO)», «(Search Engine Land)». Deduplicates by URL."""
    links, seen = [], set()
    for idx in sources or []:
        if not isinstance(idx, int) or idx < 1 or idx > len(news_list):
            continue
        n = news_list[idx - 1]
        url = (n.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        name = (n.get("source") or "Источник").strip()
        if name.startswith("TG:"):
            name = name[3:].strip()
        name = name.replace("[", "").replace("]", "") or "Источник"
        links.append(f"([{name}]({url}))")
    return " ".join(links)


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


def generate_daily_digest(news_list: list[dict], style: str = "brief",
                          period_label: str = "", max_items: int = 7) -> dict:
    """Генерирует дайджест из списка новостей через LLM.

    Args:
        news_list: словари с title, source, url, published_at, опц. total_score
        style: brief | detailed | telegram
        period_label: подпись периода для шапки («за сутки», «за неделю»)
        max_items: максимум пунктов (detailed) — для «Общего» дайджеста = 3

    Returns:
        {"title": "...", "text": "...", "news_count": N}
    """
    if not news_list:
        return {"title": "Нет данных", "text": "Нет свежих новостей за выбранный период.", "news_count": 0}

    numbered = _format_sources(news_list)
    common = {"anti_slop": ANTI_SLOP, "news_count": len(news_list), "numbered": numbered,
              "max_items": max_items}

    if style == "detailed":
        prompt = PROMPT_DETAILED.format(**common)
    elif style == "telegram":
        prompt = PROMPT_TELEGRAM.format(**common)
    else:
        prompt = PROMPT_BRIEF.format(**common)

    result = _call_llm_retry(prompt)

    if not result:
        logger.error("Digest LLM call failed")
        return {"title": "Ошибка", "text": "Не удалось сгенерировать дайджест (LLM недоступен).", "news_count": 0}

    # Keep the digest within one Telegram message (≤4096): cap to the top items.
    if isinstance(result.get("items"), list):
        result["items"] = result["items"][:max_items]
        _log_llm_selection(result, news_list, max_items, style)

    _date_head = f"📅 {_ru_date_today()}" + (f" · {period_label}" if period_label else "")
    if style == "detailed":
        text = _render_detailed(result, news_list)
        if text:
            text = _date_head + "\n\n" + text
            tags = _top_tags(news_list, 3)
            if tags:
                text += "\n\n**Главные темы дня:** " + " · ".join(tags)
    elif style == "telegram":
        text = _render_telegram(result, news_list)
        if text:
            text = _date_head + "\n\n" + text
    else:
        text = result.get("text", "")

    return {
        "title": result.get("title", "Дайджест"),
        "text": text or "Не удалось собрать пункты дайджеста.",
        "news_count": len(news_list),
    }


# ---------------------------------------------------------------------------
# Дайджест Telegram-каналов (по постам SEO/AI-каналов, со ссылками на посты)
# ---------------------------------------------------------------------------

def _format_tg_sources(news_list: list[dict]) -> str:
    """Numbered post list with hotness hints (score/viral) for the model."""
    lines = []
    for i, n in enumerate(news_list, 1):
        sc = n.get("total_score", 0)
        v = n.get("viral_score", 0)
        vl = n.get("viral_level", "") or ""
        src = n.get("source", "?")
        title = (n.get("title", "?") or "").replace("\n", " ")[:160]
        hot = f"скор {sc}, вирал {v}" + (f"/{vl}" if vl and vl != "none" else "")
        lines.append(f"{i}. [{src}] {title} ({hot})")
    return "\n".join(lines)


PROMPT_TG_CHANNELS = """Ты ведёшь живой Telegram-канал с дайджестами по SEO, AI-поиску (GEO) и digital-маркетингу.
Твоя аудитория — практикующие SEO-специалисты и маркетологи. Собери дайджест самого интересного
из других SEO/AI Telegram-каналов {period_label}.

{anti_slop}

## Дополнительно про стиль (это TG-пост, его читают с телефона)
- Каждый заголовок начинай с подходящего по смыслу эмодзи (🔥📈🤖🔎⚡️🧩💡📉🛠 и т.п. — по теме, не наугад).
- Заголовок — крючок: интрига, вопрос, неожиданный угол или конкретная цифра. Не сухой пересказ темы.
- 1-2 живых предложения: что произошло и чем это цепляет/полезно. Пиши как коллеге в чат.
- ЧЕРЕДУЙ подачу: где-то вопрос, где-то факт с цифрой, где-то «о чём спорят», где-то «что попробовать».
  Не делай все пункты по одному шаблону.
- ЗАПРЕЩЕНО начинать предложения с шаблонных «Практично:», «Полезно:», «Применимо:», «Прямой вывод:»,
  «Это сигнал…» — вплетай пользу прямо в живую фразу.
- Без канцелярита, пафоса и воды.

## Посты каналов ({news_count} шт.) — отсортированы по «горячести» (скор/виральность). Ссылайся по номеру:
{numbered}

## Задача
1. Отбери {limit} самых интересных и обсуждаемых тем {period_label}. Ориентируйся на скор и виральность,
   но выбирай то, что реально зацепит подписчика. Проходные анонсы и саморекламу каналов пропусти.
2. Похожие посты об одном событии — объедини в один пункт (перечисли все номера).
3. Каждый пункт: цепляющий заголовок + 1-2 живых предложения с сутью и тем, чем это полезно/интересно.
4. В sources укажи номера постов.
5. Расположи по убыванию интересности.

Верни строго JSON без markdown:
{{
  "title": "Цепляющий заголовок дайджеста {period_label}",
  "items": [{{"headline": "Заголовок-крючок", "summary": "1-2 живых предложения", "sources": [1]}}]
}}"""


def generate_tg_channels_digest(news_list: list[dict], period_label: str = "за сутки", limit: int = 7) -> dict:
    """Дайджест Telegram-каналов: отбирает горячее по скору/виральности, пишет живой
    дайджест со ссылками на сами посты (t.me)."""
    if not news_list:
        return {"title": "Нет данных", "text": "Нет постов TG-каналов за выбранный период.", "news_count": 0}

    prompt = PROMPT_TG_CHANNELS.format(
        anti_slop=ANTI_SLOP, period_label=period_label,
        news_count=len(news_list), numbered=_format_tg_sources(news_list), limit=limit,
    )
    result = _call_llm_retry(prompt)
    if not result:
        logger.error("TG digest LLM call failed")
        return {"title": "Ошибка", "text": "Не удалось сгенерировать дайджест (LLM недоступен).", "news_count": 0}

    if isinstance(result.get("items"), list):
        result["items"] = result["items"][:limit]
        _log_llm_selection(result, news_list, limit, "tg_channels")

    text = _render_detailed(result, news_list)
    if text:
        # Title is kept as a separate field — not duplicated into the body.
        text = f"📅 {_ru_date_today()} · {period_label}\n\n" + text
        tags = _top_tags(news_list, 3)
        if tags:
            text += "\n\n**Темы:** " + " · ".join(tags)

    return {
        "title": result.get("title", "Дайджест SEO-каналов"),
        "text": text or "Не удалось собрать пункты дайджеста.",
        "news_count": len(news_list),
    }


# ---------------------------------------------------------------------------
# Общий дайджест: лучшее из ленты + кейсов + телеграма, разбито по разделам
# ---------------------------------------------------------------------------

_SECTION_EMOJI = {"Новости": "📰", "Кейсы": "📌", "Телеграм": "📨"}


PROMPT_GENERAL = """Ты ведёшь Telegram-канал по SEO, AI и digital-маркетингу. Собери КОМПАКТНЫЙ общий
дайджест {period_label}, разбитый на три раздела: Новости, Кейсы, Телеграм.

{anti_slop}

## Материалы — у каждого помечен тип [Новости]/[Кейсы]/[Телеграм]. Ссылайся по номеру:
{numbered}

## Задача
1. Дайджест ОБЯЗАТЕЛЬНО состоит из трёх разделов — Новости, Кейсы, Телеграм. Из КАЖДОГО раздела,
   где есть материалы, возьми 4-6 лучших пунктов. ВАЖНО: баланс по разделам обязателен — НЕ сваливай
   все пункты в один раздел. Раздел без материалов пропусти; проходное и дубли пропусти.
2. Каждый пункт — ОДНА короткая ёмкая фраза по сути (что произошло/в чём польза). Без второго предложения,
   без воды и канцелярита. Поле summary оставляй пустым. НЕ начинай пункт с названия источника или канала
   (вроде «TG:…», «semai.ai:») — оно уже подставится в ссылке, сразу пиши суть.
3. В поле section укажи тип пункта строго одним из: «Новости», «Кейсы», «Телеграм».
4. В sources укажи номера источников пункта.
5. Уложи весь дайджест строго в ОДНО сообщение Telegram (до ~3500 знаков) — это жёсткий лимит.

Верни строго JSON без markdown:
{{
  "title": "Цепляющий заголовок общего дайджеста",
  "items": [{{"section": "Новости", "headline": "Одна ёмкая фраза по сути", "summary": "", "sources": [1]}}]
}}"""


def _render_general(result, news_list, seg_by_idx=None) -> str:
    """Render the general digest grouped into Новости / Кейсы / Телеграм sections.

    Раздел берём по ИСХОДНОМУ сегменту материала (seg_by_idx: индекс источника →
    «Новости»/«Кейсы»/«Телеграм»), а не по полю section из ответа LLM — иначе модель
    перекладывала обычные новости в «Кейсы». Fallback на LLM-section, если источник
    не восстановить."""
    from collections import OrderedDict
    buckets = OrderedDict((s, []) for s in ("Новости", "Кейсы", "Телеграм"))
    for it in result.get("items", []):
        srcs = it.get("sources") or []
        sec = None
        if seg_by_idx:
            for idx in srcs:
                if isinstance(idx, int) and idx in seg_by_idx:
                    sec = seg_by_idx[idx]
                    break
        if not sec:
            sec = (it.get("section") or "Новости").strip()
        if sec not in buckets:
            buckets[sec] = []
        head = (it.get("headline") or "").strip()
        body = (it.get("summary") or "").strip()
        suffix = _source_suffix(news_list, it.get("sources"))
        line = head
        if body:
            line += (" — " if line else "") + body
        if suffix:
            line += (" " if line else "") + suffix
        if line.strip():
            buckets[sec].append("• " + line.strip())
    blocks = []
    for sec, items in buckets.items():
        if items:
            blocks.append(f"**{_SECTION_EMOJI.get(sec, '•')} {sec}**\n" + "\n".join(items))
    return "\n\n".join(blocks)


def generate_general_digest(feed_news, cases_news, tg_news, period_label="за сутки") -> dict:
    """Общий дайджест: 2-3 лучших из ленты, кейсов и телеграма, короткие подписи,
    разбито по разделам, в один пост Telegram."""
    groups = [("Новости", feed_news or []), ("Кейсы", cases_news or []), ("Телеграм", tg_news or [])]
    all_news, lines = [], []
    seg_by_idx = {}  # 1-based индекс источника → раздел (жёсткая привязка, не по LLM)
    for label, lst in groups:
        for n in lst:
            all_news.append(n)
            i = len(all_news)
            seg_by_idx[i] = label
            date = (n.get("published_at") or n.get("parsed_at") or "")[:10]
            sc = n.get("total_score", 0)
            title = (n.get("title", "?") or "").replace("\n", " ")[:140]
            lines.append(f"{i}. [{label}] [{n.get('source', '?')}] {title}"
                         + (f" ({date})" if date else "") + (f" · скор {sc}" if sc else ""))
    if not all_news:
        return {"title": "Нет данных", "text": "Нет материалов за выбранный период.", "news_count": 0}

    prompt = PROMPT_GENERAL.format(anti_slop=ANTI_SLOP, period_label=period_label, numbered="\n".join(lines))
    result = _call_llm_retry(prompt)
    if not result:
        logger.error("General digest LLM call failed")
        return {"title": "Ошибка", "text": "Не удалось сгенерировать дайджест (LLM недоступен).", "news_count": 0}

    text = _render_general(result, all_news, seg_by_idx)
    if text:
        text = f"📅 {_ru_date_today()} · {period_label}\n\n" + text
    return {
        "title": result.get("title", "Общий дайджест"),
        "text": text or "Не удалось собрать пункты дайджеста.",
        "news_count": len(all_news),
    }
