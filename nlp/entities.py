"""Единая база SEO/AI-сущностей с тирами и частотами.

Термины ниши (GEO, core update, AI Overviews), эксперты/персоны (John Mueller,
Danny Sullivan) и поисковые/AI-платформы (Google, ChatGPT, Яндекс).

Используется в:
- nlp/tfidf.py — буст биграмм при совпадении с известной сущностью
- checks/viral_score.py — тир-буст виральности
- checks/deduplication.py — entity overlap с весами

freq: относительная поисковая частота 0-100 (log-scale, оценка/Keys.so).
Обновление: python scripts/fetch_entity_freq.py (Keys.so API)
Кэш: scripts/entity_freq_cache.json
"""

# Тиры: S (мега-хайп), A (крупные), B (заметные), C (нишевые)
# freq: относительная поисковая частота 0-100 (log-normalized)

CONCEPT_ENTITIES = {
    "aeo": {"tier": "A", "freq": 70, "aliases": []},
    "ai citation": {"tier": "A", "freq": 70, "aliases": []},
    "ai crawler": {"tier": "A", "freq": 70, "aliases": []},
    "ai mode": {"tier": "A", "freq": 70, "aliases": []},
    "ai overviews": {"tier": "S", "freq": 100, "aliases": []},
    "ai-overzichten": {"tier": "A", "freq": 70, "aliases": []},
    "ai-краулер": {"tier": "B", "freq": 40, "aliases": []},
    "aiによる概要": {"tier": "A", "freq": 70, "aliases": []},
    "ai検索": {"tier": "B", "freq": 40, "aliases": []},
    "answer engine": {"tier": "A", "freq": 70, "aliases": []},
    "aperçus ia": {"tier": "A", "freq": 70, "aliases": []},
    "bert": {"tier": "B", "freq": 40, "aliases": []},
    "búsqueda con ia": {"tier": "B", "freq": 40, "aliases": []},
    "core web vitals": {"tier": "B", "freq": 40, "aliases": []},
    "e-e-a-t": {"tier": "A", "freq": 70, "aliases": []},
    "generative engine optimization": {"tier": "A", "freq": 70, "aliases": []},
    "generative search": {"tier": "A", "freq": 70, "aliases": []},
    "geo": {"tier": "A", "freq": 70, "aliases": []},
    "hummingbird": {"tier": "C", "freq": 15, "aliases": []},
    "json-ld": {"tier": "B", "freq": 40, "aliases": []},
    "ki-suche": {"tier": "B", "freq": 40, "aliases": []},
    "ki-übersichten": {"tier": "A", "freq": 70, "aliases": []},
    "llms.txt": {"tier": "A", "freq": 70, "aliases": []},
    "medic update": {"tier": "C", "freq": 15, "aliases": []},
    "mum": {"tier": "B", "freq": 40, "aliases": []},
    "panda": {"tier": "B", "freq": 40, "aliases": []},
    "panoramiche ai": {"tier": "A", "freq": 70, "aliases": []},
    "penguin": {"tier": "B", "freq": 40, "aliases": []},
    "pesquisa com ia": {"tier": "B", "freq": 40, "aliases": []},
    "pigeon": {"tier": "C", "freq": 15, "aliases": []},
    "przeglądy ai": {"tier": "A", "freq": 70, "aliases": []},
    "rankbrain": {"tier": "B", "freq": 40, "aliases": []},
    "recherche ia": {"tier": "B", "freq": 40, "aliases": []},
    "resúmenes de ia": {"tier": "A", "freq": 70, "aliases": []},
    "ricerca con ia": {"tier": "B", "freq": 40, "aliases": []},
    "rich results": {"tier": "B", "freq": 40, "aliases": []},
    "rich snippets": {"tier": "B", "freq": 40, "aliases": []},
    "robots.txt": {"tier": "B", "freq": 40, "aliases": []},
    "schema markup": {"tier": "B", "freq": 40, "aliases": []},
    "schema.org": {"tier": "B", "freq": 40, "aliases": []},
    "sge": {"tier": "B", "freq": 40, "aliases": []},
    "share of voice в вебмастере": {"tier": "A", "freq": 70, "aliases": []},
    "structured data": {"tier": "B", "freq": 40, "aliases": []},
    "visões gerais com ia": {"tier": "A", "freq": 70, "aliases": []},
    "yapay zeka genel bakışları": {"tier": "A", "freq": 70, "aliases": []},
    "yati": {"tier": "A", "freq": 70, "aliases": []},
    "агс": {"tier": "A", "freq": 70, "aliases": []},
    "баден-баден": {"tier": "A", "freq": 70, "aliases": []},
    "вега": {"tier": "A", "freq": 70, "aliases": []},
    "видимость в алисе": {"tier": "A", "freq": 70, "aliases": []},
    "генеративный поиск": {"tier": "A", "freq": 70, "aliases": []},
    "ии-поиск": {"tier": "A", "freq": 70, "aliases": []},
    "королёв": {"tier": "B", "freq": 40, "aliases": []},
    "минусинск": {"tier": "A", "freq": 70, "aliases": []},
    "нейроответы": {"tier": "A", "freq": 70, "aliases": []},
    "нейроответы яндекса": {"tier": "A", "freq": 70, "aliases": []},
    "нейросеть": {"tier": "B", "freq": 40, "aliases": []},
    "оригами": {"tier": "A", "freq": 70, "aliases": []},
    "палех": {"tier": "B", "freq": 40, "aliases": []},
    "тайфун": {"tier": "A", "freq": 70, "aliases": []},
    "цитирование в ии": {"tier": "A", "freq": 70, "aliases": []},
    "цитируемость в ии": {"tier": "A", "freq": 70, "aliases": []},
}

PERSON_ENTITIES = {
    "danny sullivan": {"tier": "A", "freq": 70, "aliases": []},
    "gary illyes": {"tier": "A", "freq": 70, "aliases": []},
    "google core update": {"tier": "A", "freq": 70, "aliases": []},
    "john mueller": {"tier": "A", "freq": 70, "aliases": []},
    "judge mehta": {"tier": "A", "freq": 70, "aliases": []},
    "martin splitt": {"tier": "B", "freq": 40, "aliases": []},
    "search central": {"tier": "B", "freq": 40, "aliases": []},
    "search console": {"tier": "B", "freq": 40, "aliases": []},
    "search liaison": {"tier": "A", "freq": 70, "aliases": []},
    "андромеда": {"tier": "B", "freq": 40, "aliases": []},
    "быстрые ответы": {"tier": "B", "freq": 40, "aliases": []},
    "гэри илльес": {"tier": "B", "freq": 40, "aliases": []},
    "джон мюллер": {"tier": "B", "freq": 40, "aliases": []},
    "икс": {"tier": "B", "freq": 40, "aliases": []},
    "илльес": {"tier": "B", "freq": 40, "aliases": []},
    "колдунщик": {"tier": "B", "freq": 40, "aliases": []},
    "мюллер": {"tier": "B", "freq": 40, "aliases": []},
    "обновление икс": {"tier": "B", "freq": 40, "aliases": []},
    "турбо-страницы": {"tier": "B", "freq": 40, "aliases": []},
    "эпос": {"tier": "B", "freq": 40, "aliases": []},
    "яндекс бизнес": {"tier": "B", "freq": 40, "aliases": []},
    "яндекс вебмастер": {"tier": "B", "freq": 40, "aliases": []},
    "яндекс справочник": {"tier": "B", "freq": 40, "aliases": []},
}

PLATFORM_ENTITIES = {
    "baidu": {"tier": "C", "freq": 15, "aliases": []},
    "bing": {"tier": "B", "freq": 40, "aliases": []},
    "chatgpt": {"tier": "S", "freq": 100, "aliases": []},
    "chatgpt search": {"tier": "A", "freq": 70, "aliases": []},
    "claude": {"tier": "B", "freq": 40, "aliases": []},
    "claudebot": {"tier": "B", "freq": 40, "aliases": []},
    "copilot": {"tier": "B", "freq": 40, "aliases": []},
    "duckduckgo": {"tier": "C", "freq": 15, "aliases": []},
    "gemini": {"tier": "S", "freq": 100, "aliases": []},
    "google": {"tier": "S", "freq": 100, "aliases": []},
    "google-extended": {"tier": "A", "freq": 70, "aliases": []},
    "gptbot": {"tier": "A", "freq": 70, "aliases": []},
    "grok": {"tier": "B", "freq": 40, "aliases": []},
    "llama": {"tier": "C", "freq": 15, "aliases": []},
    "mistral": {"tier": "C", "freq": 15, "aliases": []},
    "naver": {"tier": "C", "freq": 15, "aliases": []},
    "openai": {"tier": "S", "freq": 100, "aliases": []},
    "perplexity": {"tier": "S", "freq": 100, "aliases": []},
    "perplexitybot": {"tier": "B", "freq": 40, "aliases": []},
    "yandex": {"tier": "S", "freq": 100, "aliases": []},
    "yandexgpt": {"tier": "A", "freq": 70, "aliases": []},
    "алиса ai": {"tier": "A", "freq": 70, "aliases": []},
    "нейро": {"tier": "A", "freq": 70, "aliases": []},
    "поиск с алисой": {"tier": "A", "freq": 70, "aliases": []},
}

# Tier -> viral boost (используется в viral_score.py)
TIER_BOOST = {"S": 30, "A": 15, "B": 8, "C": 3}

# --- Lookup index: строится при импорте для быстрого поиска ---

_LOOKUP = {}  # {"ai overviews": {"name": "ai overviews", "type": "concept", "tier": "S", "freq": 100}, ...}


def _build_lookup():
    """Строит плоский индекс alias->entity для быстрого поиска в тексте."""
    for name, data in CONCEPT_ENTITIES.items():
        entry = {"name": name, "type": "concept", "tier": data["tier"], "freq": data["freq"]}
        _LOOKUP[name] = entry
        for alias in data.get("aliases", []):
            if alias and alias not in _LOOKUP:
                _LOOKUP[alias] = entry

    for name, data in PERSON_ENTITIES.items():
        entry = {"name": name, "type": "person", "tier": data["tier"], "freq": data["freq"]}
        _LOOKUP[name] = entry
        for alias in data.get("aliases", []):
            if alias and alias not in _LOOKUP:
                _LOOKUP[alias] = entry

    for name, data in PLATFORM_ENTITIES.items():
        entry = {"name": name, "type": "platform", "tier": data["tier"], "freq": data["freq"]}
        _LOOKUP[name] = entry
        for alias in data.get("aliases", []):
            if alias and alias not in _LOOKUP:
                _LOOKUP[alias] = entry


_build_lookup()


import re
from functools import lru_cache

# Короткие ключи (<=3 символов) требуют word boundary, иначе ловят подстроки
_SHORT_KEY_THRESHOLD = 3

# Прекомпилированные regex для коротких ключей (один раз при загрузке модуля)
_SHORT_KEY_PATTERNS = {}
# Отсортированные ключи (один раз)
_SORTED_KEYS = sorted(_LOOKUP.keys(), key=len, reverse=True)

for _k in _SORTED_KEYS:
    if len(_k) <= _SHORT_KEY_THRESHOLD:
        _SHORT_KEY_PATTERNS[_k] = re.compile(r'\b' + re.escape(_k) + r'\b')


@lru_cache(maxsize=256)
def _find_entities_cached(text_lower: str) -> tuple:
    """Кешированный поиск сущностей. Возвращает tuple для hashability."""
    found = {}
    for key in _SORTED_KEYS:
        if len(key) <= _SHORT_KEY_THRESHOLD:
            if _SHORT_KEY_PATTERNS[key].search(text_lower):
                entry = _LOOKUP[key]
                canonical = entry["name"]
                if canonical not in found:
                    found[canonical] = entry
        else:
            if key in text_lower:
                entry = _LOOKUP[key]
                canonical = entry["name"]
                if canonical not in found:
                    found[canonical] = entry
    return tuple(sorted(found.values(), key=lambda e: e["freq"], reverse=True))


def find_entities(text: str) -> list[dict]:
    """Находит все известные сущности в тексте.

    Возвращает список: [{"name": "gta 6", "type": "game", "tier": "S", "freq": 100}, ...]
    Сортировка: по freq (высокие сначала).
    Результаты кешируются (LRU 256).
    """
    return list(_find_entities_cached(text.lower()))


def get_entity_boost(text: str) -> tuple[int, list[dict]]:
    """Считает суммарный буст от сущностей в тексте.

    Возвращает (total_boost, entities).
    Буст считается от лучшего тира найденных сущностей (не суммируется).
    """
    entities = find_entities(text)
    if not entities:
        return 0, []

    best_tier = entities[0]["tier"]  # уже отсортировано по freq
    boost = TIER_BOOST.get(best_tier, 0)

    return boost, entities
