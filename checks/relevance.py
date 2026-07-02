"""Relevance check tuned to the project's niche: SEO, AI-поиск/GEO и digital-маркетинг.

Counts distinct topical keywords (RU+EN) in title+description+text. More on-topic
signal → higher relevance. A small noise list lightly penalises clearly off-topic
posts (crypto pumps, gambling, unrelated politics).
"""

# Узкие тематические ключи (вес 12): однозначно про SEO / AI-поиск / digital.
# Подстроки подобраны так, чтобы ловить словоформы
# («ранжирован» → ранжирование/ранжируется), избегая совсем коротких неоднозначных.
NARROW_KEYWORDS = [
    # SEO core
    "seo", "поисковая оптимизация", "оптимизац сайт", "продвижение сайт",
    "ранжирован", "ranking", "индексац", "indexing", "переобход", "краулинг", "crawl",
    "выдач", "serp", "поисковик", "поисковых систем", "search engine", "органическ трафик",
    "organic traffic", "позиции сайт", "ключев запрос", "ключев слов", "keyword", "семантик",
    "сниппет", "snippet", "featured snippet", "robots.txt", "sitemap", "карта сайта",
    "метатег", "meta title", "title и description", "микроразметк", "schema",
    "обратные ссылки", "обратных ссылок", "ссылочн", "backlink", "линкбилдинг", "анкор",
    "core update", "broad core", "апдейт", "обновление алгоритма", "алгоритм google",
    "e-e-a-t", "eeat", "поведенческ фактор", "трафик сайта",
    # Search platforms / tools
    "вебмастер", "search console", "вордстат",
    "ga4", "веб-аналитик", "метрика", "ahrefs", "semrush", "keys.so", "topvisor",
    # AI / GEO / AEO
    "chatgpt", "gpt", "claude", "gemini", "perplexity", "llm", "ии-поиск", "ai-поиск",
    "ai overviews", "нейроответ", "нейро яндекс", "генеративн", "generative", "yandexgpt",
    "aeo", "llms.txt", "ии-агент", "ai agent", "цитируемость в ии", "видимость в ии",
    # Digital marketing: специфичные термины
    "контент-маркетинг", "контент-план", "контент-стратег", "копирайтинг", "тексты для сайта",
    "контекстн реклам", "контекстная реклама", "google ads", "яндекс директ", "ppc", "рся",
    "email-рассылк", "digital-маркетинг", "интернет-маркетинг", "smm",
]

# Широкие ключи (вес 3): тематический фон, сам по себе тему не подтверждает.
# Раньше они весили как узкие, и почти любой пост набирал 100.
BROAD_KEYWORDS = [
    "google", "гугл", "яндекс", "yandex", "нейросет", "нейронн сет",
    "искусственн интеллект", "machine learning", "geo",
    "конверси", "ctr", "лендинг", "воронк", "маркетинг", "маркетолог",
    "реклам", "продвижени", "трафик", "соцсет", "вконтакте",
    "telegram-канал", "телеграм-канал", "блогер", "контент",
]

# Лёгкий штраф за явно нетематический контент.
NOISE_KEYWORDS = [
    "крипт", "биткоин", "bitcoin", "форекс", "казино", "ставки на спорт", "букмекер",
]


def check_relevance(news: dict) -> dict:
    text = (news.get("title", "") + " " + news.get("description", "") + " " + news.get("plain_text", "")).lower()

    narrow_hits = sum(1 for kw in NARROW_KEYWORDS if kw in text)
    broad_hits = sum(1 for kw in BROAD_KEYWORDS if kw in text)
    noise_hits = sum(1 for kw in NOISE_KEYWORDS if kw in text)

    # Узкий хит — 12, широкий — 3: 2 узких + 3 широких → 33, 5 узких → 60,
    # 8+ узких → 100. Пост без единого узкого термина не проходит,
    # сколько бы фоновых слов («маркетинг», «трафик») в нём ни было.
    score = min(100, narrow_hits * 12 + broad_hits * 3) - noise_hits * 15
    passes = narrow_hits >= 1 and (noise_hits == 0 or (narrow_hits + broad_hits) >= noise_hits * 3)
    return {
        "score": max(0, score),
        "topic_hits": narrow_hits + broad_hits,
        "narrow_hits": narrow_hits,
        "noise_hits": noise_hits,
        "pass": passes,
    }
