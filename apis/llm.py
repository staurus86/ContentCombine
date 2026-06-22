import json
import logging
import os
from openai import OpenAI
import config
from core.circuit_breaker import _api_circuit_open

logger = logging.getLogger(__name__)

# Primary: OpenRouter key 1, Fallback: OpenRouter key 2
_API_KEYS = [k for k in [
    config.OPENAI_API_KEY,
    os.getenv("OPENAI_API_KEY_2", ""),
] if k]

client = OpenAI(
    api_key=_API_KEYS[0] if _API_KEYS else "",
    base_url=config.OPENAI_BASE_URL,
)

PROMPT_TREND_FORECAST = """
Ты — старший аналитик игрового медиа-портала с 10+ годами опыта. Оцени трендовый потенциал новости.

## Данные для анализа
Заголовок: {title}
Текст (фрагмент): {text}
Ключевые биграммы: {bigrams}
Частота поискового запроса (Keys.so, ws): {keyso_freq}
Данные Google Trends: {trends}

## Критерии оценки trend_score (0-100):
- 80-100: AAA-релиз, утечка года, скандал крупного издателя, неожиданный анонс на E3/Gamescom/TGA
- 60-79: крупное обновление популярной игры, сделка/поглощение, значимый патч с контентом
- 40-59: стабильный интерес к теме, новость в рамках ожидаемого цикла (бета-тест, ранний доступ)
- 20-39: нишевая тема, узкая аудитория, перепубликация уже известного
- 0-19: устаревшая информация, малоизвестный продукт без хайпа

## Факторы усиления: наличие даты релиза, цены, скриншотов/трейлера, эксклюзивности
## Факторы ослабления: «по слухам», «возможно», отсутствие конкретики, пересказ старых событий

Ответь строго JSON (без markdown):
{{
  "trend_score": число_0_100,
  "reasoning": "краткое обоснование в 1-2 предложения",
  "peak_window": "через X часов / сейчас / в течение дня",
  "recommendation": "publish_now / schedule / skip",
  "confidence": "high / medium / low"
}}
"""

PROMPT_MERGE_ANALYSIS = """
Ты — главный редактор игрового портала. Перед тобой несколько новостей об одном и том же событии из разных источников.

Задача: объединить их в одну качественную новость, взяв лучшее из каждого источника.

## Новости для объединения:
{news_list}

## Правила объединения:
1. Заголовок — самый точный и привлекательный, без clickbait
2. Текст — собери все уникальные факты, убери дублирование, сохрани хронологию
3. Если источники противоречат друг другу — укажи обе версии
4. Укажи все числовые данные (даты, цены, платформы) без потерь
5. Язык: русский, деловой тон, 3-5 абзацев

Верни строго JSON (без markdown):
{{
  "merged_title": "объединённый заголовок",
  "merged_text": "полный объединённый текст со всеми фактами",
  "unique_facts": ["уникальный факт из источника 1", "факт из источника 2"],
  "best_source": "имя наиболее полного источника",
  "sources_used": число_использованных_источников
}}
"""

PROMPT_KEYSO_QUERIES = """
Ты — SEO-специалист в игровом медиа. Составь поисковые запросы для анализа конкуренции и трафика по теме новости.

## Тема новости: {title}
## Ключевые биграммы: {bigrams}
## Регион поиска: {region}

## Правила:
1. Предложи ровно 10 запросов на русском языке
2. Запросы 1-3: точные (название игры/события + действие, напр. «gta 6 дата выхода»)
3. Запросы 4-6: информационные (связанные вопросы, напр. «когда выйдет gta 6»)
4. Запросы 7-8: навигационные (бренды, платформы, напр. «rockstar games новости»)
5. Запросы 9-10: длинный хвост (low-competition, напр. «gta 6 системные требования пк 2025»)
6. Не дублируй биграммы — расширяй и дополняй их

Верни строго JSON: {{"queries": ["запрос 1", "запрос 2", ...]}}
"""


def _call_llm_raw(prompt: str, key_index: int = 0, news_id: str = "") -> dict | None:
    """Один вызов LLM с конкретным ключом."""
    import time as _t
    key = _API_KEYS[key_index] if key_index < len(_API_KEYS) else _API_KEYS[0]
    c = OpenAI(api_key=key, base_url=config.OPENAI_BASE_URL)
    t0 = _t.time()
    response = c.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=config.LLM_TEMPERATURE,
        timeout=config.LLM_TIMEOUT_SECONDS,
    )
    latency_ms = int((_t.time() - t0) * 1000)
    text = response.choices[0].message.content
    logger.info("LLM response (key %d): %s", key_index + 1, text[:200])

    # Track API cost (best-effort)
    try:
        usage = getattr(response, 'usage', None)
        tokens_in = getattr(usage, 'prompt_tokens', 0) if usage else 0
        tokens_out = getattr(usage, 'completion_tokens', 0) if usage else 0
        # Estimate cost (approximate for common models)
        cost = (tokens_in * 0.15 + tokens_out * 0.6) / 1_000_000  # gpt-4o-mini pricing
        from core.observability import track_api_call
        track_api_call("llm", endpoint="chat.completions", model=config.LLM_MODEL,
                       tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
                       latency_ms=latency_ms, news_id=news_id)
    except Exception:
        pass

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    return json.loads(cleaned)


def _call_llm(prompt: str) -> dict | None:
    """Вызывает LLM с retry, fallback ключами и rate limiting."""
    import time as _time
    from apis.cache import rate_check, rate_increment
    if not rate_check("llm"):
        logger.warning("LLM rate limit exceeded")
        return None
    if _api_circuit_open("llm"):
        logger.warning("LLM circuit breaker open — skipping")
        return None
    for i in range(len(_API_KEYS)):
        # Up to 3 attempts per key (JSON errors are retryable)
        for attempt in range(3):
            try:
                result = _call_llm_raw(prompt, i)
                rate_increment("llm")  # count only on actual call
                if result is not None:
                    return result
            except json.JSONDecodeError as e:
                logger.warning("LLM key %d JSON parse error (attempt %d/3): %s", i, attempt + 1, e)
                if attempt < 2:
                    _time.sleep(2 * (attempt + 1))  # 2s, 4s
                    continue
                # Last attempt failed — try next key
                break
            except Exception as e:
                logger.warning("LLM key %d error (attempt %d/3): %s", i, attempt + 1, e)
                if attempt < 2:
                    _time.sleep(3 * (attempt + 1))  # 3s, 6s
                    continue
                break
    logger.error("All LLM keys failed")
    return None


def forecast_trend(title: str, text: str, bigrams: list,
                   keyso_freq: int, trends: dict) -> dict | None:
    prompt = PROMPT_TREND_FORECAST.format(
        title=title,
        text=text[:2000],
        bigrams=bigrams,
        keyso_freq=keyso_freq,
        trends=trends,
    )
    return _call_llm(prompt)


def merge_news(news_list: list[dict]) -> dict | None:
    formatted = "\n\n".join(
        f"Источник: {n['source']}\nЗаголовок: {n['title']}\nТекст: {n.get('plain_text', '')[:1000]}"
        for n in news_list
    )
    prompt = PROMPT_MERGE_ANALYSIS.format(news_list=formatted)
    return _call_llm(prompt)


REWRITE_STYLES = {
    "news": {
        "desc": "Информационная заметка",
        "instructions": """Стиль: информационная новостная заметка.
Правила:
- Заголовок: чёткий, фактологический, без оценок, до 80 символов
- Первый абзац (лид): кто, что, когда, где — вся суть за 2 предложения
- Тело: 2-3 абзаца, только проверенные факты, хронология событий
- Тон: нейтральный, деловой, без эмоций и мнений автора
- НЕ используй: восклицательные знаки, оценочные прилагательные, слова «удивительный», «невероятный»
- Укажи источник информации если он есть в тексте""",
    },
    "seo": {
        "desc": "SEO-оптимизированная статья",
        "instructions": """Стиль: SEO-оптимизированная статья для поисковых систем.
Правила:
- Заголовок: содержит главное ключевое слово, до 70 символов, привлекательный для клика
- Структура: используй подзаголовки (## H2), списки, выделение ключевых слов
- Первый абзац: обязательно содержит основное ключевое слово в первом предложении
- Текст: 4-5 абзацев, плотность ключевых слов 2-3%, естественная речь
- Включи LSI-ключевые слова (синонимы, связанные термины)
- seo_title: точно до 60 символов, с ключевым словом в начале
- seo_description: точно до 155 символов, с призывом к действию
- Теги: 5-7 тегов включая название игры, жанр, платформу""",
    },
    "review": {
        "desc": "Обзорная статья с мнением",
        "instructions": """Стиль: авторский обзор / аналитика.
Правила:
- Заголовок: содержит мнение или оценку, может быть провокационным вопросом
- Вступление: зацепи читателя контекстом — почему это важно
- Тело: 4-5 абзацев, анализ ситуации, плюсы и минусы, сравнение с аналогами
- Включи авторское мнение, но подкрепи его аргументами
- Заключение: вывод и прогноз — что это значит для индустрии/игроков
- Тон: экспертный, вдумчивый, допускается субъективность""",
    },
    "clickbait": {
        "desc": "Кликбейтный заголовок и интрига",
        "instructions": """Стиль: кликбейт для максимального CTR.
Правила:
- Заголовок: интригующий, с эмоциональным крючком, вопросом или числом. Примеры паттернов: «Вот почему...», «Это изменит всё», «5 причин...», «Никто не ожидал...»
- Первый абзац: усиль интригу, но НЕ раскрывай всё сразу
- Тело: 3-4 абзаца, раскрывай информацию постепенно, держи внимание
- Используй: короткие предложения, восклицательные знаки, эмоциональные слова
- Финал: неожиданный поворот или сильный вывод
- НЕ ври и не искажай факты — привлекай внимание подачей, а не ложью""",
    },
    "short": {
        "desc": "Короткая заметка",
        "instructions": """Стиль: ультра-короткая заметка / брифинг.
Правила:
- Заголовок: максимально лаконичный, до 60 символов
- Текст: ровно 2-3 предложения, только самая суть
- Первое предложение — главный факт
- Второе — ключевая деталь или контекст
- Третье (опционально) — дата/платформа/цена если релевантно
- НЕ используй: вводные слова, лишние прилагательные, повторы
- Идеально для дайджестов и push-уведомлений""",
    },
    "social": {
        "desc": "Пост для соцсетей",
        "instructions": """Стиль: пост для Telegram / VK / Twitter.
Правила:
- Заголовок: цепляющий, с эмодзи в начале, до 60 символов
- Текст: 3-5 коротких строк, между ними пустые строки для читаемости
- Используй 2-4 эмодзи к месту (🎮 ⚡ 🔥 💰 📅 🏆 и т.д.)
- Тон: неформальный, дружеский, как будто рассказываешь другу
- Добавь призыв: вопрос к аудитории или «Что думаете?»
- В конце: 3-5 хештегов через пробел (#игры #gaming и т.д.)
- Общая длина: до 280 символов для Twitter, до 500 для Telegram""",
    },
}

PROMPT_REWRITE = """
Ты — профессиональный игровой журналист и редактор с 8+ годами опыта в игровых медиа. Твоя задача — переписать новость так, чтобы она была уникальной, информативной и соответствовала заданному стилю.

## Оригинал
Заголовок: {title}
Текст: {text}

## Стиль и инструкции
{style_instructions}

## Язык: {language}

## Общие требования:
- Сохрани ВСЕ факты, даты, цены, имена, названия из оригинала — ничего не выдумывай
- Текст должен быть полностью уникальным (перефразирование, не копипаст)
- Избегай штампов: «стоит отметить», «как известно», «в настоящее время»
- Каждый абзац начинай с новой мысли, не повторяй одно и то же
- seo_title обязательно содержит название игры/продукта и до 60 символов
- seo_description — привлекательное описание для поисковой выдачи, до 155 символов
- tags — 5 релевантных тегов (название игры, жанр, платформа, издатель, тема)

## Правила оформления цен и валют:
- ВСЕГДА используй символы валют: $, €, £, ₽ — НЕ пиши словами «долларов», «евро», «фунтов»
- Формат: $49.99, €39.99, £29.99, 3999 ₽ (символ валюты перед суммой для $, €, £; после суммы для ₽)
- Если в оригинале цена указана словами — переведи в символьный формат

## Правила кавычек:
- В русском тексте ВСЕГДА используй кавычки-ёлочки: «текст», НЕ „текст" и НЕ "текст"
- Вложенные кавычки: «внешние „внутренние" кавычки»

## Правила ссылок на источники:
- Если продукт/демо/бета ПУБЛИЧНО доступен всем — НЕ ссылайся на мнение одного конкретного издания как на авторитетное. Вместо «по мнению IGN» пиши «игроки отмечают» или описывай факты без привязки к одному источнику
- Ссылайся на конкретное издание ТОЛЬКО когда информация эксклюзивна (утечка, инсайд, интервью)

## Структура текста:
- ОБЯЗАТЕЛЬНО добавь минимум один подзаголовок <h2> в текст (разбивает текст на логические блоки)
- Контекст «ранее случилось» или «напомним» — ВСЕГДА выноси в отдельный абзац в конце, не склеивай с основными пунктами
- Если материал большой (интервью, подробный разбор) — используй несколько <h2> подзаголовков
- Не пытайся уместить весь контент в 3 пункта — используй столько абзацев, сколько нужно по смыслу

## Правила оформления названий:
- Названия игр, фильмов, студий на английском языке — пиши БЕЗ кавычек: Super Mario Galaxy, не «Super Mario Galaxy»
- Русские названия и цитаты — в кавычках-ёлочках: «Атомное сердце», «Смута»
- Если есть официальная русская локализация названия — используй её
- Англоязычные должности и термины переводи естественно по-русски: co-director → сорежиссёр, lead designer → ведущий дизайнер, community manager → комьюнити-менеджер
- НЕ делай буквальных калек с английского (НЕ «со-директор», НЕ «лид-дизайнер»)

Верни строго JSON без markdown-обёртки:
{{
  "title": "заголовок новости (яркий, информативный, до 100 символов)",
  "feed_description": "описание для ленты — краткая суть новости в 1-2 предложения (до 200 символов), без спойлеров основного текста",
  "seo_title": "SEO заголовок публикации до 60 символов с ключевым словом",
  "seo_description": "SEO описание публикации до 155 символов, привлекательное для поисковой выдачи",
  "text": "полный текст новости с <h2>подзаголовками</h2> — основной контент для страницы публикации",
  "tags": ["тег1", "тег2", "тег3", "тег4", "тег5"]
}}
"""


def rewrite_news(title: str, text: str, style: str = "news", language: str = "русский") -> dict | None:
    style_data = REWRITE_STYLES.get(style, REWRITE_STYLES["news"])
    prompt = PROMPT_REWRITE.format(
        title=title,
        text=text[:3000],
        style_instructions=style_data["instructions"],
        language=language,
    )
    return _call_llm(prompt)


def translate_title(title: str, source_lang: str = "auto") -> dict | None:
    """Автоперевод заголовка на русский с определением языка."""
    from apis.cache import cache_get, cache_set, cache_key
    ck = cache_key("translate", title)
    cached = cache_get(ck)
    if cached:
        return cached
    prompt = f"""Определи язык заголовка и переведи его на русский. Если заголовок уже на русском — верни как есть.

Заголовок: {title}

Ответь строго JSON без markdown:
{{
  "original": "оригинальный заголовок",
  "translated": "перевод на русский",
  "source_lang": "en/ru/de/fr/etc",
  "is_russian": true/false
}}"""
    result = _call_llm(prompt)
    if result:
        cache_set(ck, result, ttl=86400 * 7)
    return result


def ai_recommendation(title: str, text: str, source: str, checks: dict) -> dict | None:
    """AI-рекомендация: публиковать или нет, с обоснованием."""
    from apis.cache import cache_get, cache_set, cache_key
    ck = cache_key("ai_rec", title)
    cached = cache_get(ck)
    if cached:
        return cached

    checks_summary = ""
    for name, data in checks.items():
        if isinstance(data, dict) and "score" in data:
            checks_summary += f"- {name}: {data['score']}/100 ({'pass' if data.get('pass') else 'fail'})\n"

    prompt = f"""Ты — главный редактор игрового портала. Оцени новость и дай рекомендацию.

Заголовок: {title}
Источник: {source}
Текст (фрагмент): {text[:1500]}

Результаты автопроверок:
{checks_summary}

Оцени по критериям:
1. Интересна ли тема аудитории игрового портала?
2. Достаточно ли информации для полноценной публикации?
3. Есть ли эксклюзивность или уникальный ракурс?
4. Актуальна ли новость (не устарела)?

Ответь строго JSON:
{{
  "verdict": "publish / rewrite / skip",
  "confidence": 0.0-1.0,
  "reason": "краткое обоснование в 1-2 предложения",
  "suggested_angle": "предложи ракурс подачи если verdict=rewrite",
  "priority": "high / medium / low"
}}"""
    result = _call_llm(prompt)
    if result:
        cache_set(ck, result, ttl=3600)
    return result


def suggest_keyso_queries(title: str, bigrams: list, region: str = "RU") -> list[str]:
    prompt = PROMPT_KEYSO_QUERIES.format(
        title=title,
        bigrams=bigrams,
        region=region,
    )
    result = _call_llm(prompt)
    if result and "queries" in result:
        return result["queries"]
    return []
