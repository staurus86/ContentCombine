"""Автоматические теги по таксономии."""

# SEO/search-marketing taxonomy (multilingual RU+EN + universal tokens).
# Keywords lowercase, substring match; phrases/>=4 chars to avoid noise.
TAXONOMY = {
    "algo_update": {
        "label": "Алгоритм/Апдейт",
        "keywords": [
            "core update", "broad core", "spam update", "helpful content",
            "algorithm update", "algo update", "google update", "rolling out",
            "rollout", "unconfirmed update",
            "апдейт", "обновление алгоритма", "базовый апдейт", "выкатили апдейт",
            "прокатился апдейт", "переранжирование", "тайфун", "оригами", "вега",
            "минусинск", "баден-баден",
        ],
    },
    "penalty": {
        "label": "Фильтр/Санкции",
        "keywords": [
            "manual action", "penalty", "deindexed", "deindexing", "demotion",
            "spam policy", "scaled content abuse", "site reputation abuse",
            "parasite seo", "cloaking", "unnatural links",
            "фильтр", "под фильтр", "санкции", "пессимизация", "выпал из индекса",
            "исключён из поиска", "забанили", "накрутка пф", "аффилиат", "деиндексация",
        ],
    },
    "ai_search": {
        "label": "AI-поиск/GEO",
        "keywords": [
            "ai overviews", "ai mode", "generative search", "answer engine",
            "chatgpt", "perplexity", "gemini", "copilot", "ai citation",
            "ai crawler", "gptbot", "llms.txt", "generative engine optimization",
            "zero-click",
            "нейроответы", "нейросет", "ии-поиск", "поиск с алисой", "yandexgpt",
            "генеративный поиск", "цитирование в ии", "geo", "aeo",
        ],
    },
    "technical": {
        "label": "Технический SEO",
        "keywords": [
            "core web vitals", "robots.txt", "structured data", "schema markup",
            "rich results", "indexing", "crawl budget", "noindex", "canonical",
            "javascript seo", "rendering", "page experience", "googlebot", "sitemap",
            "индексация", "переобход", "краулинг", "микроразметка", "редирект",
            "скорость загрузки", "дубли страниц",
        ],
    },
    "content": {
        "label": "Контент/E-E-A-T",
        "keywords": [
            "e-e-a-t", "helpful content", "ai content", "ai-generated content",
            "thin content", "content pruning", "topical authority", "quality content",
            "контент", "экспертность", "ai-тексты", "сгенерированный контент",
            "некачественный контент", "полезный контент",
        ],
    },
    "links": {
        "label": "Ссылки",
        "keywords": [
            "backlink", "link building", "link spam", "toxic links", "disavow",
            "anchor text", "guest post",
            "обратные ссылки", "ссылочн", "покупные ссылки", "линкбилдинг",
            "анкор", "токсичные ссылки", "крауд-ссылки", "обесценивание ссылок",
        ],
    },
    "local": {
        "label": "Локальный SEO",
        "keywords": [
            "local seo", "google business profile", "google maps",
            "local pack", "nap consistency", "citations",
            "локальное seo", "яндекс бизнес", "яндекс справочник", "2гис",
            "карты", "геозависим", "локальная выдача",
        ],
    },
    "analytics": {
        "label": "Аналитика/Данные",
        "keywords": [
            "search console", "google search console", "analytics 4", "ga4",
            "looker studio", "impressions", "click-through rate", "ctr",
            "share of voice", "tracking",
            "вебмастер", "метрика", "вордстат", "обновление икс", "отчёт", "аналитика",
        ],
    },
    "tool": {
        "label": "Инструменты",
        "keywords": [
            "ahrefs", "semrush", "screaming frog", "sistrix", "serpstat",
            "surfer", "new feature", "launches", "integration", "new tool",
            "обновил инструмент", "новый инструмент", "новая функция", "интеграция",
        ],
    },
    "industry": {
        "label": "Индустрия",
        "keywords": [
            "acquisition", "merger", "antitrust", "monopoly", "doj", "lawsuit",
            "funding", "investment", "ipo", "layoffs", "partnership", "revenue",
            "поглощение", "слияние", "антимонопол", "иск", "инвестиции", "сделка",
            "партнёрство", "выручка", "увольнения",
        ],
    },
    "research": {
        "label": "Исследование/Кейс",
        "keywords": [
            "study", "research", "case study", "survey", "benchmark", "data shows",
            "experiment", "analysis of", "report finds", "we analyzed",
            "исследование", "кейс", "опрос", "эксперимент", "по данным", "анализ",
        ],
    },
    "ranking": {
        "label": "Позиции/Трафик",
        "keywords": [
            "traffic drop", "traffic loss", "lost rankings", "ranking drop",
            "visibility drop", "recovery", "serp", "volatility", "ranking surge",
            "обвал трафика", "падение позиций", "просадка", "потеря трафика",
            "вылет из топа", "восстановление трафика", "штормит выдача",
        ],
    },
    "ads_ppc": {
        "label": "Реклама/PPC",
        "keywords": [
            "google ads", "performance max", "pmax", "microsoft ads", "bing ads",
            "ppc", "paid search", "cpc", "roas", "ad campaign",
            "яндекс директ", "контекстная реклама", "рся", "ставки", "кампани",
        ],
    },
    "event": {
        "label": "События/Конференции",
        "keywords": [
            "brightonseo", "google i/o", "search central live", "webinar",
            "conference", "keynote", "announced at", "live blog",
            "конференция", "вебинар", "митап", "выступил на", "доклад",
        ],
    },
}


def auto_tag(news: dict) -> list[dict]:
    """Возвращает список тегов для новости."""
    plain = news.get("plain_text", "") or news.get("description", "") or ""
    text = (news.get("title", "") + " " + plain).lower()
    tags = []
    for tag_id, tag_info in TAXONOMY.items():
        hits = sum(1 for kw in tag_info["keywords"] if kw in text)
        if hits >= 1:
            tags.append({
                "id": tag_id,
                "label": tag_info["label"],
                "hits": hits,
                "confidence": min(1.0, hits / 3),
            })
    tags.sort(key=lambda t: t["hits"], reverse=True)
    return tags
