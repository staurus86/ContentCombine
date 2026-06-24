"""Relevance check tuned to the project's niche: SEO, AI-поиск/GEO и digital-маркетинг.

Counts distinct topical keywords (RU+EN) in title+description+text. More on-topic
signal → higher relevance. A small noise list lightly penalises clearly off-topic
posts (crypto pumps, gambling, unrelated politics).
"""

# Тематические ключевые слова. Подстроки подобраны так, чтобы ловить словоформы
# («ранжирован» → ранжирование/ранжируется), избегая совсем коротких неоднозначных.
TOPIC_KEYWORDS = [
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
    "google", "гугл", "яндекс", "yandex", "вебмастер", "search console", "вордстат",
    "ga4", "веб-аналитик", "метрика", "ahrefs", "semrush", "keys.so", "topvisor",
    # AI / GEO / AEO
    "нейросет", "нейронн сет", "искусственн интеллект", "machine learning",
    "chatgpt", "gpt", "claude", "gemini", "perplexity", "llm", "ии-поиск", "ai-поиск",
    "ai overviews", "нейроответ", "нейро яндекс", "генеративн", "generative", "yandexgpt",
    "geo", "aeo", "llms.txt", "ии-агент", "ai agent", "цитируемость в ии", "видимость в ии",
    # Content & digital marketing
    "контент-маркетинг", "контент-план", "контент-стратег", "копирайтинг", "тексты для сайта",
    "контекстн реклам", "контекстная реклама", "google ads", "яндекс директ", "ppc", "рся",
    "конверси", "ctr", "лендинг", "воронк", "smm", "email-рассылк", "digital-маркетинг",
    "интернет-маркетинг", "маркетинг", "маркетолог", "реклам", "продвижени", "трафик",
    "веб-аналитик", "соцсет", "вконтакте", "telegram-канал", "телеграм-канал", "блогер", "контент",
]

# Лёгкий штраф за явно нетематический контент.
NOISE_KEYWORDS = [
    "крипт", "биткоин", "bitcoin", "форекс", "казино", "ставки на спорт", "букмекер",
]


def check_relevance(news: dict) -> dict:
    text = (news.get("title", "") + " " + news.get("description", "") + " " + news.get("plain_text", "")).lower()

    topic_hits = sum(1 for kw in TOPIC_KEYWORDS if kw in text)
    noise_hits = sum(1 for kw in NOISE_KEYWORDS if kw in text)

    # 2 тематических совпадения → 24, 5 → 60, 8+ → ~100. Заголовок весит как и тело
    # (заголовок учтён в text). Off-topic шум немного снижает балл.
    score = min(100, topic_hits * 12) - noise_hits * 15
    passes = topic_hits >= 2 and (noise_hits == 0 or topic_hits >= noise_hits * 3)
    return {
        "score": max(0, score),
        "topic_hits": topic_hits,
        "noise_hits": noise_hits,
        "pass": passes,
    }
