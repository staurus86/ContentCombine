"""Daily digest generation via LLM.

Detailed digest returns STRUCTURED items: the model references each source by its
number, and we render the real source links ourselves (no URL hallucination).
Brief and telegram styles return free-form text.
"""

import logging
import re
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
- Если у новости стоит метка [✓ подтверждено Google] — это апдейт, сверенный с официальным
  Search Status Dashboard. Отрази факт подтверждения («Google официально подтвердил…») и
  ставь такой пункт выше слухов на ту же тему. Саму метку в текст не копируй.

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


def _update_badge(n: dict) -> str:
    """Метка «подтверждено Google» для новости-апдейта, сверенной с Search Status.
    Пусто, если это не апдейт или он не подтверждён. Fails open."""
    try:
        from checks.update_verification import verify_update
        v = verify_update(n.get("title", ""), n.get("plain_text", "") or n.get("description", "") or "")
        return f" [✓ подтверждено Google: {v['name']}]" if v.get("confirmed") else ""
    except Exception:
        return ""


def _render_prompt(template: str, **kw) -> str:
    """Подстановка плейсхолдеров с защитой от битого шаблона. Промпты теперь
    редактируются в Настройках: опечатка ({numberd}), лишний {ключ} или
    раздвоенная JSON-скобка ломают str.format (KeyError/ValueError) — дайджест
    падал бы целиком. Фолбэк: тупой replace известных {ключей}, остальное как есть."""
    try:
        return template.format(**kw)
    except Exception as e:
        logger.warning("Digest prompt template broken (%s) — safe-replace fallback", e)
        out = template
        for k, v in kw.items():
            out = out.replace("{" + k + "}", str(v))
        return out


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
        lines.append(f"{i}. [{src}] {title}" + (f" ({', '.join(hints)})" if hints else "") + _update_badge(n))
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


PROMPT_DETAILED = """Ты — выпускающий редактор отраслевого Telegram-издания про SEO, AI, поиск, контент, аналитику и digital-маркетинг.

Составь подробный, но лаконичный дайджест свежих новостей для профессиональной аудитории: SEO-специалистов, digital-маркетологов, редакторов, владельцев сайтов и продуктовых команд.

Дайджест должен отвечать не только на вопрос «что случилось», но и на вопрос «почему это важно и что меняется на практике».

{anti_slop}

## Новости ({news_count} шт.)

Ниже список новостей. Ссылайся на них только по номеру:

{numbered}

## Задача

1. Отбери только самое значимое — строго не больше {max_items} пунктов.

2. Пропусти:

   * проходные анонсы без практического значения;
   * перепечатки одной и той же новости;
   * слабые инфоповоды без последствий для SEO, AI, поиска, контента, аналитики или digital;
   * рекламные публикации без фактуры;
   * новости, где невозможно понять суть или проверить значимость.

3. Несколько новостей об одном событии объедини в один пункт.
   В sources укажи все номера источников, которые относятся к этому событию.

4. Расположи пункты по убыванию важности:

   * сначала изменения, которые могут повлиять на SEO, AI-видимость, трафик, индексацию, аналитику, рекламу или контентные процессы;
   * затем крупные обновления инструментов, платформ и поисковых систем;
   * затем полезные отраслевые наблюдения и менее критичные новости.

5. Для каждого пункта подготовь:

   * headline — короткий, конкретный заголовок без кликбейта;
   * summary — 1–2 плотных предложения: что произошло, почему это важно, что это меняет на практике;
   * sources — номера источников.

6. В headline запрещено:

   * начинать с названия источника;
   * писать «Новость:», «Источник сообщает», «В материале говорится»;
   * использовать капс, кликбейт, общие слова и пустые формулировки;
   * выдумывать масштаб события, цифры или последствия, которых нет в источниках.

7. В summary запрещено:

   * пересказывать новость водой;
   * делать неподтверждённые прогнозы;
   * добавлять советы, не вытекающие из материала;
   * писать больше 2 предложений;
   * использовать канцелярит и общие фразы вроде «это может быть полезно для бизнеса».

8. Хороший summary должен быть в формате:

   * что изменилось;
   * кого это касается;
   * что теперь стоит проверить, учесть или пересмотреть.

9. Дайджест должен умещаться в одно сообщение Telegram.
   Лучше взять меньше пунктов, но сделать их плотными и полезными.

10. Перед финальным ответом проверь:

* пунктов не больше {max_items};
* новости об одном событии объединены;
* sources существуют в исходном списке;
* пункты отсортированы по важности;
* summary состоит из 1–2 предложений;
* JSON валидный;
* нет markdown, комментариев и текста вне JSON.

Верни строго JSON без markdown:

{{
"title": "Заголовок дайджеста — по главной теме дня, конкретно",
"items": [
{{"headline": "Заголовок пункта", "summary": "1–2 предложения по сути: что произошло, почему это важно и что меняет на практике.", "sources": [1, 4]}}
]
}}"""


PROMPT_BRIEF = """Ты — выпускающий редактор отраслевого Telegram-издания про SEO, AI, поиск, контент, аналитику и digital-маркетинг.

Составь краткий дайджест свежих новостей для профессиональной аудитории: SEO-специалистов, digital-маркетологов, редакторов, владельцев сайтов и продуктовых команд.

Формат brief — это не список и не пересказ всех новостей, а короткая редакционная выжимка дня: только главное, по убыванию значимости.

{anti_slop}

## Новости ({news_count} шт.)

Ниже список новостей. Используй их как исходные материалы:

{numbered}

## Задача

1. Напиши связный текст из 5–7 предложений.

2. Каждое предложение должно быть отдельным значимым событием, изменением или тенденцией.

3. Расположи предложения по убыванию важности:

   * сначала новости, которые могут повлиять на SEO, AI-видимость, поиск, трафик, индексацию, аналитику, рекламу или контентные процессы;
   * затем крупные обновления платформ, инструментов и поисковых систем;
   * затем менее критичные, но полезные отраслевые наблюдения.

4. Не пытайся охватить все новости.
   Выбери только самое важное и отбрось проходные анонсы, перепечатки, рекламу и слабые инфоповоды.

5. Несколько новостей об одном событии объедини в одну мысль.
   Не повторяй одно и то же разными словами.

6. Каждое предложение должно отвечать хотя бы на один из вопросов:

   * что изменилось;
   * почему это важно;
   * кого это касается;
   * что теперь стоит учитывать на практике.

7. Не указывай источники, номера новостей, ссылки, названия каналов и технические пометки.

8. Запрещено:

   * писать списком;
   * использовать markdown;
   * добавлять вводные фразы вроде «Сегодня в дайджесте»;
   * начинать предложения с «Также», «Кроме того», «Ещё одна новость»;
   * использовать кликбейт, капс, воду и канцелярит;
   * выдумывать факты, цифры, выводы или последствия, которых нет в материалах;
   * делать неподтверждённые прогнозы.

9. Текст должен быть плотным и естественным для Telegram: 5–7 коротких предложений, без лишних пояснений.

10. Заголовок title сделай коротким и конкретным — по главной теме дня или общей тенденции.
    Без кликбейта, эмодзи и искусственного пафоса.

11. Перед финальным ответом проверь:

    * text состоит из 5–7 предложений;
    * каждое предложение несёт отдельный смысл;
    * нет перечня источников и номеров;
    * нет дублей;
    * нет markdown;
    * JSON валидный;
    * нет текста вне JSON.

Верни строго JSON без markdown:

{{"title": "Короткий конкретный заголовок дайджеста", "text": "Связный текст из 5–7 предложений, где каждое предложение — отдельное важное событие или тенденция."}}"""


PROMPT_TELEGRAM = """Ты — редактор Telegram-канала для SEO-специалистов, digital-маркетологов, редакторов, владельцев сайтов и продуктовых команд.

Составь живой, компактный пост-дайджест по свежим новостям SEO, AI, поиска, контента, аналитики и digital-маркетинга.

Формат поста — не сухая сводка, а удобная Telegram-выжимка: коротко, по делу, с понятной пользой для специалиста.

{anti_slop}

## Новости ({news_count} шт.)

Ниже список новостей. Ссылайся на них только по номеру:

{numbered}

## Задача

1. Отбери 4–6 самых значимых пунктов.

2. Пропусти:

   * проходные анонсы без практического значения;
   * перепечатки одной и той же новости;
   * рекламные публикации без фактуры;
   * слабые инфоповоды без последствий для SEO, AI, поиска, контента, аналитики или digital;
   * материалы, где невозможно понять суть.

3. Несколько новостей об одном событии объедини в один пункт.
   В sources укажи все номера источников, которые относятся к этому событию.

4. Расположи пункты по убыванию важности:

   * сначала изменения, которые могут повлиять на SEO, AI-видимость, трафик, индексацию, аналитику, рекламу или контентные процессы;
   * затем крупные обновления поисковиков, AI-сервисов, платформ и инструментов;
   * затем полезные отраслевые наблюдения.

5. Каждый пункт должен начинаться с одного релевантного эмодзи.

6. Для каждого пункта подготовь:

   * headline — одна живая фраза с эмодзи в начале;
   * summary — оставь пустым: "";
   * sources — номера источников.

7. В headline нужно передать:

   * что произошло;
   * почему это важно;
   * что специалисту стоит учесть на практике.

8. В headline запрещено:

   * начинать с названия источника, сайта или канала;
   * писать «Новость:», «Источник сообщает», «В материале говорится»;
   * использовать кликбейт, капс и искусственный пафос;
   * добавлять воду и канцелярит;
   * писать больше двух коротких фраз;
   * выдумывать факты, цифры, выводы или последствия, которых нет в источниках.

9. Тон: живой, профессиональный, без официоза.
   Не превращай пост в корпоративную сводку.

10. Заголовок title сделай коротким и цепляющим — по главной теме дня или общей тенденции.
    Без капса, кликбейта и лишних эмодзи.

11. В hashtags добавь 3–5 тематических хештегов:

    * только по реальным темам выбранных пунктов;
    * без случайных общих тегов;
    * без дублей;
    * на русском или английском, если так привычнее для темы: #SEO, #AI, #Google, #Яндекс, #Контент, #Аналитика.

12. Пост должен умещаться в одно сообщение Telegram.
    Лучше меньше пунктов, но выше плотность пользы.

13. Перед финальным ответом проверь:

    * пунктов от 4 до 6;
    * каждый headline начинается с эмодзи;
    * summary везде пустой;
    * sources существуют в исходном списке;
    * новости об одном событии объединены;
    * hashtags от 3 до 5;
    * JSON валидный;
    * нет markdown, комментариев и текста вне JSON.

Верни строго JSON без markdown:

{{
"title": "Короткий цепляющий заголовок поста",
"items": [
{{"headline": "🔍 Суть пункта одной-двумя живыми фразами", "summary": "", "sources": [2]}}
],
"hashtags": ["#SEO", "#AI", "#Google"]
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


def _tg_handle(url: str) -> str:
    """@хендл канала из ссылки на пост: t.me/shakinru/123 → «@shakinru».
    Пусто для приватных каналов (t.me/c/<id>/<msg>) и любых нерегулярных ссылок."""
    m = re.search(r"t\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})/\d+", url or "")
    return f"@{m.group(1)}" if m else ""


def _source_suffix(news_list, sources) -> str:
    """Compact source links per item: издания подписаны названием
    «(Search Engine Land)», telegram-каналы — хендлом «(@drmaxseo)».
    Дедуп по URL И по имени источника:
    пункт, собранный из девяти материалов одного сайта, давал подпись
    «(semai.ai)» девять раз подряд (2026-08-04). Одно издание — одна ссылка,
    ведёт на первый материал."""
    links, seen, seen_names = [], set(), set()
    for idx in sources or []:
        if not isinstance(idx, int) or idx < 1 or idx > len(news_list):
            continue
        n = news_list[idx - 1]
        url = (n.get("url") or "").strip()
        if not url or url in seen:
            continue
        name = (n.get("source") or "Источник").strip()
        if name.startswith("TG:"):
            name = _tg_handle(url) or name[3:].strip()
        name = name.replace("[", "").replace("]", "") or "Источник"
        if name.lower() in seen_names:
            continue
        seen.add(url)
        seen_names.add(name.lower())
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


def selected_ids(result: dict, news_list: list[dict]) -> list:
    """id материалов, на которые модель реально сослалась в пунктах дайджеста.

    Это и есть решения редактора — в отличие от списка кандидатов, который
    формирует сам скоринг. На них учатся веса источников и считается качество
    отбора в аналитике."""
    out, seen = [], set()
    for it in (result or {}).get("items", []) or []:
        for idx in (it.get("sources") or []):
            if isinstance(idx, int) and 1 <= idx <= len(news_list):
                nid = news_list[idx - 1].get("id")
                if nid and nid not in seen:
                    seen.add(nid)
                    out.append(nid)
    return out


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
        prompt = _render_prompt(PROMPT_DETAILED, **common)
    elif style == "telegram":
        prompt = _render_prompt(PROMPT_TELEGRAM, **common)
    else:
        prompt = _render_prompt(PROMPT_BRIEF, **common)

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
        "selected_ids": selected_ids(result, news_list),
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


PROMPT_TG_CHANNELS = """Ты ведёшь живой Telegram-канал с дайджестами по SEO, AI-поиску, GEO, AEO, контенту, аналитике и digital-маркетингу.

Твоя аудитория — практикующие SEO-специалисты, маркетологи, редакторы, владельцы сайтов и продуктовые команды. Собери дайджест самого интересного из других SEO/AI Telegram-каналов {period_label}.

Это не сухая подборка ссылок, а редакторская выжимка: что обсуждают в профессиональной среде, какие идеи зацепили, какие наблюдения стоит проверить, какие споры или кейсы могут быть полезны в работе.

{anti_slop}

## Посты каналов ({news_count} шт.)

Посты отсортированы по «горячести»: скор, виральность, реакции, обсуждаемость.
Ссылайся на них только по номеру:

{numbered}

## Стиль Telegram-поста

1. Пиши так, будто объясняешь коллеге в рабочем чате: живо, коротко, по делу.

2. Каждый headline начинай с подходящего по смыслу эмодзи:

   * 🔥 — горячая тема, спор, резкий вывод;
   * 📈 — рост, цифры, результаты, кейсы;
   * 🤖 — AI, нейропоиск, LLM, автоматизация;
   * 🔎 — поиск, SERP, индексация, выдача;
   * ⚡️ — быстрый инсайт, срочное изменение, сильный сигнал;
   * 🧩 — методика, разбор, связка факторов;
   * 💡 — идея, наблюдение, гипотеза;
   * 📉 — падение, риск, проблема;
   * 🛠 — инструмент, чеклист, практическое действие.

3. Эмодзи выбирай по смыслу, не наугад. Не используй один и тот же эмодзи подряд без необходимости.

4. Headline — это крючок, а не название темы. Используй:

   * вопрос;
   * конкретную цифру;
   * неожиданный угол;
   * спорный тезис;
   * сильный практический вывод;
   * короткую формулировку «что попробовать».

5. Summary — 1–2 живых предложения:

   * что произошло или что обсуждают;
   * почему это зацепило;
   * чем это полезно, спорно или применимо на практике.

6. Чередуй подачу. Не делай все пункты по одному шаблону:

   * где-то вопрос;
   * где-то факт с цифрой;
   * где-то «о чём спорят»;
   * где-то «что проверить»;
   * где-то короткий вывод из кейса;
   * где-то наблюдение по рынку.

7. Запрещено начинать headline или summary с шаблонов:

   * «Практично:»
   * «Полезно:»
   * «Применимо:»
   * «Прямой вывод:»
   * «Это сигнал…»
   * «Автор пишет…»
   * «В канале рассказали…»
   * «Пост посвящён…»

8. Пользу вплетай прямо в живую фразу, без служебных вступлений.

## Задача

1. Отбери {limit} самых интересных и обсуждаемых тем {period_label}.

2. Ориентируйся на скор и виральность, но не слепо. Выбирай то, что реально зацепит профессиональную аудиторию:

   * сильные SEO-наблюдения;
   * практические кейсы;
   * разборы апдейтов;
   * новые подходы к AI-видимости, GEO и AEO;
   * полезные инструменты;
   * спорные тезисы;
   * цифры, которые можно использовать как ориентир;
   * идеи, которые хочется проверить на своих проектах.

3. Пропускай:

   * саморекламу каналов без пользы;
   * анонсы вебинаров, курсов и услуг без самостоятельной ценности;
   * проходные мнения без фактуры;
   * дубли;
   * пересказы новостей без добавленной мысли;
   * посты, где невозможно понять суть;
   * слишком локальные обсуждения без пользы для широкой SEO/digital-аудитории.

4. Похожие посты об одном событии, споре, инструменте или кейсе объедини в один пункт.
   В sources укажи все номера постов, которые относятся к этому пункту.

5. Каждый пункт должен содержать:

   * headline — цепляющий заголовок-крючок с эмодзи в начале;
   * summary — 1–2 живых предложения с сутью и пользой;
   * sources — номера постов.

6. Расположи пункты по убыванию интересности:

   * сначала самые обсуждаемые и практически ценные темы;
   * затем сильные кейсы, инструменты и методики;
   * затем полезные наблюдения и менее горячие идеи.

7. Не начинай headline с названия канала, автора или источника.
   Источник уже будет подставлен по номеру.

8. Не выдумывай факты, цифры, выводы, эмоции аудитории или масштаб обсуждения, если этого нет в материалах.

9. Не превращай дайджест в официальную новостную сводку.
   Текст должен читаться как Telegram-пост, который хочется пролистать до конца.

10. Перед финальным ответом проверь:

    * выбрано ровно или не больше {limit} пунктов;
    * похожие посты объединены;
    * sources существуют в исходном списке;
    * каждый headline начинается с эмодзи;
    * эмодзи соответствуют смыслу;
    * пункты не написаны по одному шаблону;
    * нет саморекламы и проходных анонсов;
    * summary состоит из 1–2 предложений;
    * JSON валидный;
    * нет markdown, комментариев и текста вне JSON.

Верни строго JSON без markdown:

{{
"title": "Цепляющий заголовок дайджеста {period_label}",
"items": [
{{"headline": "🔥 Заголовок-крючок", "summary": "1–2 живых предложения: что обсуждают, почему это цепляет и что можно вынести для работы.", "sources": [1]}}
]
}}"""


def generate_tg_channels_digest(news_list: list[dict], period_label: str = "за сутки", limit: int = 7) -> dict:
    """Дайджест Telegram-каналов: отбирает горячее по скору/виральности, пишет живой
    дайджест со ссылками на сами посты (t.me)."""
    if not news_list:
        return {"title": "Нет данных", "text": "Нет постов TG-каналов за выбранный период.", "news_count": 0}

    prompt = _render_prompt(PROMPT_TG_CHANNELS,
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
        "selected_ids": selected_ids(result, news_list),
    }


# ---------------------------------------------------------------------------
# Общий дайджест: лучшее из ленты + кейсов + телеграма, разбито по разделам
# ---------------------------------------------------------------------------

_SECTION_EMOJI = {"Новости": "📰", "Кейсы": "📌", "Телеграм": "📨"}


PROMPT_GENERAL = """Ты ведёшь Telegram-канал для SEO-специалистов, маркетологов и владельцев digital-проектов. Собери КОМПАКТНЫЙ общий дайджест {period_label} по SEO, AI, поиску, контенту, аналитике и digital-маркетингу.

Дайджест должен быть полезным для практикующего специалиста: меньше шума, больше конкретики, что изменилось, что можно применить, на что обратить внимание.

{anti_slop}

## Материалы

У каждого материала помечен тип: [Новости] / [Кейсы] / [Телеграм].
Ссылайся на материалы только по их номерам:

{numbered}

## Задача

1. Собери дайджест строго из трёх возможных разделов:

   * Новости
   * Кейсы
   * Телеграм

2. Разделы должны идти именно в таком порядке: Новости → Кейсы → Телеграм.

3. Из каждого раздела, где есть достойные материалы, выбери 3–4 лучших пункта.
   Если качественных материалов меньше — возьми меньше. Всего в дайджесте не больше 10 пунктов.
   Если раздел пустой или там только проходные/дублирующиеся материалы — пропусти раздел полностью.

4. Баланс обязателен: не собирай весь дайджест из одного типа материалов, если в других разделах есть сильные пункты.

5. Отбирай пункты по приоритету:

   * важные изменения в поиске, SEO, AI, Яндексе, Google, Telegram, аналитике и рекламных системах;
   * практические кейсы с выводами, цифрами, методикой или полезной логикой;
   * посты с нестандартной мыслью, инструментом, наблюдением, чеклистом или применимой идеей;
   * материалы, которые дают специалисту повод что-то проверить, внедрить или пересмотреть.

6. Пропускай:

   * дубли одной и той же новости;
   * рекламные и самопиарные материалы без пользы;
   * очевидные пересказы без нового смысла;
   * слабые мнения без фактуры;
   * материалы, где невозможно понять суть;
   * слишком узкие новости без практической ценности.

7. Каждый пункт — ОДНА короткая фраза по сути, СТРОГО не длиннее 120 знаков:

   * что произошло;
   * в чём польза;
   * почему это важно;
   * какой вывод можно сделать.

   Ориентир: 80–110 знаков. Не пиши по два события через запятую или тире — бери одно,
   самое важное, остальное отбрось. Не добавляй пояснений в скобках.

8. В headline запрещено:

   * писать второе предложение;
   * начинать с названия источника, сайта или канала;
   * использовать формулы вроде «TG:», «semai.ai:», «Автор пишет», «В материале говорится»;
   * добавлять воду, канцелярит, оценочные клише и общие слова;
   * выдумывать факты, цифры или выводы, которых нет в источниках;
   * склеивать два разных события в одну фразу ради полноты;
   * превышать 120 знаков.

9. Поле summary всегда оставляй пустым: "".

10. В поле section укажи строго одно из трёх значений:

* "Новости"
* "Кейсы"
* "Телеграм"

11. В sources укажи номера источников, на которых основан пункт.
    Если несколько материалов говорят об одном и том же — объедини их в один пункт и укажи несколько номеров.

12. Заголовок title должен быть коротким, цепляющим и естественным для Telegram.
    Без кликбейта, капса и искусственного пафоса.

13. Весь дайджест должен помещаться в ОДНО сообщение Telegram. Считай сумму всех headline —
    она должна быть до 1200 знаков. Ссылки на источники подставляются автоматически,
    писать их не нужно и в лимит они не входят.
    Лучше меньше пунктов, но выше плотность пользы.

14. Перед финальным ответом проверь:

* есть ли баланс по разделам;
* нет ли дублей;
* все ли sources существуют в списке материалов;
* каждый headline состоит из одной фразы и укладывается в 120 знаков;
* сумма длин всех headline не превышает 1200 знаков;
* пунктов не больше 10;
* summary везде пустой;
* JSON валидный;
* нет markdown, комментариев и текста вне JSON.

Верни строго JSON без markdown:

{{
"title": "Цепляющий заголовок общего дайджеста",
"items": [
{{"section": "Новости", "headline": "Одна ёмкая фраза по сути", "summary": "", "sources": [1]}}
]
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
                         + (f" ({date})" if date else "") + (f" · скор {sc}" if sc else "")
                         + _update_badge(n))
    if not all_news:
        return {"title": "Нет данных", "text": "Нет материалов за выбранный период.", "news_count": 0}

    prompt = _render_prompt(PROMPT_GENERAL, anti_slop=ANTI_SLOP, period_label=period_label, numbered="\n".join(lines))
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
        "selected_ids": selected_ids(result, all_news),
    }
