import json
import logging
import os
from openai import OpenAI
import config
from core.circuit_breaker import _api_circuit_open

logger = logging.getLogger(__name__)

# Primary: ключ из config (env или Настройки), Fallback: запасной ключ из env.
# Читается на каждый вызов, а не один раз при импорте: ключ, сохранённый через
# ⚙ Настройки, обновляет config.OPENAI_API_KEY в памяти — без этого новый ключ
# начинал работать только после рестарта сервиса.
def _api_keys() -> list[str]:
    return [k for k in [config.OPENAI_API_KEY, os.getenv("OPENAI_API_KEY_2", "")] if k]

# Browser-like UA: the gateway sits behind Cloudflare, which serves a
# «Just a moment…» challenge to the openai-SDK's default httpx User-Agent.
# A real-browser UA passes the managed challenge (verified against the gateway).
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

def get_client(key: str = "") -> OpenAI:
    """Клиент гейтвея с актуальным ключом и базовым URL из config."""
    keys = _api_keys()
    return OpenAI(
        api_key=key or (keys[0] if keys else ""),
        base_url=config.OPENAI_BASE_URL,
        default_headers={"User-Agent": BROWSER_UA},
    )

PROMPT_TREND_FORECAST = """
Ты — старший аналитик отраслевого медиа про SEO, AI-поиск и digital-маркетинг с 10+ годами опыта. Оцени трендовый потенциал новости.

## Данные для анализа
Заголовок: {title}
Текст (фрагмент): {text}
Ключевые биграммы: {bigrams}
Частота поискового запроса (Keys.so, ws): {keyso_freq}
Данные Google Trends: {trends}

## Критерии оценки trend_score (0-100):
- 80-100: подтверждённый core update / ручные санкции / утечка API Google, крупный сдвиг в AI-поиске (AI Overviews, релиз топовой модели), громкий антимонопольный иск
- 60-79: заметное обновление алгоритма или инструмента, сделка/поглощение, значимое изменение выдачи или правил индексации
- 40-59: стабильный интерес к теме, новость в рамках ожидаемого цикла (бета-фичи, анонсы, кейсы)
- 20-39: нишевая тема, узкая аудитория, перепубликация уже известного
- 0-19: устаревшая информация, малозначимый повод без охвата

## Факторы усиления: подтверждение от Google/Яндекса, конкретные цифры/данные, официальный источник, дата
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
Ты — главный редактор отраслевого медиа про SEO, AI-поиск и digital-маркетинг. Перед тобой несколько новостей об одном и том же событии из разных источников.

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


def _call_llm_raw(prompt: str, key_index: int = 0, news_id: str = "", model: str = "") -> dict | None:
    """Один вызов LLM с конкретным ключом и моделью (по умолчанию — основная)."""
    import time as _t
    keys = _api_keys()
    key = keys[key_index] if key_index < len(keys) else (keys[0] if keys else "")
    model = model or config.LLM_MODEL
    c = get_client(key)
    t0 = _t.time()
    response = c.chat.completions.create(
        model=model,
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
        track_api_call("llm", endpoint="chat.completions", model=model,
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
    """Вызывает LLM с фолбэк-цепочкой моделей, запасным ключом и rate limiting.
    При таймауте/ошибке основной модели (напр. Cloudflare-флап на гейтвее) пробует
    следующую модель — это лечит повторяющиеся «LLM недоступен». Ограничено по числу
    вызовов, чтобы worst-case не висел: модели×1 + один запасной ключ."""
    from apis.cache import rate_check, rate_increment
    if not rate_check("llm"):
        logger.warning("LLM rate limit exceeded")
        return None
    if _api_circuit_open("llm"):
        logger.warning("LLM circuit breaker open — skipping")
        return None

    chain = [config.LLM_MODEL] + [m for m in config.LLM_FALLBACK_MODELS if m != config.LLM_MODEL]
    for mi, model in enumerate(chain):
        try:
            result = _call_llm_raw(prompt, 0, model=model)
            rate_increment("llm")  # count only on an actual call
            if result is not None:
                if mi > 0:
                    logger.info("LLM fallback model succeeded: %s (primary %s failed)", model, config.LLM_MODEL)
                return result
        except json.JSONDecodeError as e:
            logger.warning("LLM model %s JSON parse error — next model: %s", model, e)
        except Exception as e:
            logger.warning("LLM model %s error — next model: %s", model, e)

    # Final fallback: a second API key with the primary model (key-specific issue).
    if len(_api_keys()) > 1:
        try:
            result = _call_llm_raw(prompt, 1, model=config.LLM_MODEL)
            rate_increment("llm")
            if result is not None:
                return result
        except Exception as e:
            logger.warning("LLM second-key fallback failed: %s", e)

    logger.error("All LLM models/keys failed")
    return None


def _call_llm_cached(namespace: str, prompt: str, ttl: int = 86400) -> dict | None:
    """_call_llm с кэшем по хэшу prompt. Ключ строится из самого промпта, поэтому
    совпадает ровно с тем, что уходит в LLM — рассинхрона ключа и запроса нет.
    Только для детерминированных вызовов (forecast/merge), где повтор того же входа
    должен давать тот же ответ. НЕ применять к rewrite (пользователь ждёт вариацию)."""
    from apis.cache import cache_get, cache_set, cache_key
    ck = cache_key(namespace, prompt)
    cached = cache_get(ck)
    if cached is not None:
        return cached
    result = _call_llm(prompt)
    if result:
        cache_set(ck, result, ttl=ttl)
    return result


def translate_titles_en(titles: list[str]) -> dict[str, str]:
    """Батч-перевод заголовков на английский для кросс-язычной кластеризации трендов.

    Один мировой сюжет (Google update) на EN/DE/RU/FR лексически не пересекается —
    tfidf его не склеивает. Перевод на общий язык (EN — большинство источников)
    решает это. Кэш на каждый заголовок (7 дней), батч 40 шт/вызов — холодное
    3-дневное окно стоит ~2 вызова gpt-4o-mini. Fail-open: без LLM возвращает
    оригиналы, кластеризация работает как раньше."""
    from apis.cache import cache_get, cache_set, cache_key
    out = {}
    todo = []
    for t in titles:
        if not t:
            continue
        ck = cache_key("tr_en", t)
        cached = cache_get(ck)
        if cached is not None:
            out[t] = cached
        else:
            todo.append(t)

    BATCH = 40
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(chunk))
        prompt = f"""Translate each numbered headline to English. Keep product/brand names as-is.
Return STRICT JSON without markdown, mapping the number to the English translation:
{{"1": "English headline", "2": "..."}}

Headlines:
{numbered}"""
        result = _call_llm(prompt)
        if not isinstance(result, dict):
            # fail-open: этот чанк остаётся в оригинале, кэш не травим
            for t in chunk:
                out[t] = t
            continue
        for i, t in enumerate(chunk):
            en = result.get(str(i + 1))
            en = en.strip() if isinstance(en, str) and en.strip() else t
            out[t] = en
            cache_set(cache_key("tr_en", t), en, ttl=86400 * 7)
    return out


def forecast_trend(title: str, text: str, bigrams: list,
                   keyso_freq: int, trends: dict) -> dict | None:
    prompt = PROMPT_TREND_FORECAST.format(
        title=title,
        text=text[:2000],
        bigrams=bigrams,
        keyso_freq=keyso_freq,
        trends=trends,
    )
    return _call_llm_cached("forecast", prompt)


def merge_news(news_list: list[dict]) -> dict | None:
    formatted = "\n\n".join(
        f"Источник: {n['source']}\nЗаголовок: {n['title']}\nТекст: {n.get('plain_text', '')[:1000]}"
        for n in news_list
    )
    prompt = PROMPT_MERGE_ANALYSIS.format(news_list=formatted)
    return _call_llm_cached("merge", prompt)


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
- Теги: 5-7 тегов включая тему, продукт/инструмент, поисковую систему/платформу""",
    },
    "review": {
        "desc": "Обзорная статья с мнением",
        "instructions": """Стиль: авторский обзор / аналитика.
Правила:
- Заголовок: содержит мнение или оценку, может быть провокационным вопросом
- Вступление: зацепи читателя контекстом — почему это важно
- Тело: 4-5 абзацев, анализ ситуации, плюсы и минусы, сравнение с аналогами
- Включи авторское мнение, но подкрепи его аргументами
- Заключение: вывод и прогноз — что это значит для SEO-специалистов и бизнеса
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
- Используй 2-4 эмодзи к месту (🔍 📈 🤖 ⚡ 🔥 📉 💡 и т.д.)
- Тон: неформальный, дружеский, как будто рассказываешь другу
- Добавь призыв: вопрос к аудитории или «Что думаете?»
- В конце: 3-5 хештегов через пробел (#SEO #AI #маркетинг и т.д.)
- Общая длина: до 280 символов для Twitter, до 500 для Telegram""",
    },
}

PROMPT_REWRITE = """
Ты — профессиональный редактор с 8+ годами опыта в медиа про SEO, AI-поиск и digital-маркетинг. Твоя задача — переписать новость так, чтобы она была уникальной, информативной и соответствовала заданному стилю.

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
- seo_title обязательно содержит название продукта/темы и до 60 символов
- seo_description — привлекательное описание для поисковой выдачи, до 155 символов
- tags — 5 релевантных тегов (тема, продукт/инструмент, поисковая система/платформа, компания)

## Правила оформления цен и валют:
- ВСЕГДА используй символы валют: $, €, £, ₽ — НЕ пиши словами «долларов», «евро», «фунтов»
- Формат: $49.99, €39.99, £29.99, 3999 ₽ (символ валюты перед суммой для $, €, £; после суммы для ₽)
- Если в оригинале цена указана словами — переведи в символьный формат

## Правила кавычек:
- В русском тексте ВСЕГДА используй кавычки-ёлочки: «текст», НЕ „текст" и НЕ "текст"
- Вложенные кавычки: «внешние „внутренние" кавычки»

## Правила ссылок на источники:
- Если продукт/инструмент/обновление ПУБЛИЧНО доступен всем — НЕ ссылайся на мнение одного конкретного издания как на авторитетное. Вместо «по мнению Search Engine Land» пиши «специалисты отмечают» или описывай факты без привязки к одному источнику
- Ссылайся на конкретное издание ТОЛЬКО когда информация эксклюзивна (утечка, инсайд, интервью)

## Структура текста:
- ОБЯЗАТЕЛЬНО добавь минимум один подзаголовок <h2> в текст (разбивает текст на логические блоки)
- Контекст «ранее случилось» или «напомним» — ВСЕГДА выноси в отдельный абзац в конце, не склеивай с основными пунктами
- Если материал большой (интервью, подробный разбор) — используй несколько <h2> подзаголовков
- Не пытайся уместить весь контент в 3 пункта — используй столько абзацев, сколько нужно по смыслу

## Правила оформления названий:
- Названия продуктов, инструментов, компаний на английском языке — пиши БЕЗ кавычек: Search Console, не «Search Console»
- Русские названия и цитаты — в кавычках-ёлочках: «Яндекс Вебмастер», «Топвизор»
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

    prompt = f"""Ты — главный редактор отраслевого медиа про SEO, AI-поиск и digital-маркетинг. Оцени новость и дай рекомендацию.

Заголовок: {title}
Источник: {source}
Текст (фрагмент): {text[:1500]}

Результаты автопроверок:
{checks_summary}

Оцени по критериям:
1. Интересна ли тема аудитории SEO-специалистов и маркетологов?
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
