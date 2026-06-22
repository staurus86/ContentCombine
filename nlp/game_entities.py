"""Единая база игровых сущностей с тирами и частотами.

Используется в:
- nlp/tfidf.py — буст биграмм при совпадении с известной сущностью
- checks/viral_score.py — замена BIG_TITLES на тиры
- checks/deduplication.py — entity overlap с весами
- checks/ner.py — может импортировать отсюда

freq: реальная частота поиска 0-100 (log-scale, Steam API + estimates, 2026-03-11).
Обновление: python scripts/fetch_entity_freq.py (Keys.so API)
Кэш: scripts/entity_freq_cache.json
"""

# Тиры: S (мега-хайп), A (крупные), B (заметные), C (нишевые)
# freq: реальная поисковая частота 0-100 (log-normalized, обновлена 2026-03-11)

GAME_ENTITIES = {
    # === S-TIER: максимальный хайп, любая новость — событие ===
    "gta 6":                {"tier": "S", "freq": 100, "aliases": ["gta vi", "grand theft auto 6", "grand theft auto vi"]},
    "minecraft":            {"tier": "S", "freq": 91, "aliases": []},
    "fortnite":             {"tier": "S", "freq": 90, "aliases": []},
    "gta":                  {"tier": "S", "freq": 76, "aliases": ["grand theft auto"]},
    "zelda":                {"tier": "S", "freq": 78, "aliases": ["the legend of zelda", "tears of the kingdom", "breath of the wild"]},
    "call of duty":         {"tier": "S", "freq": 82, "aliases": ["cod", "modern warfare", "warzone", "black ops"]},

    # === A-TIER: крупные франшизы, стабильный интерес ===
    "elder scrolls 6":      {"tier": "A", "freq": 66, "aliases": ["elder scrolls vi", "tes 6", "tes vi"]},
    "half-life 3":          {"tier": "A", "freq": 73, "aliases": ["half life 3"]},
    "elden ring":           {"tier": "A", "freq": 66, "aliases": ["elden ring nightreign"]},
    "cyberpunk 2077":       {"tier": "A", "freq": 55, "aliases": ["cyberpunk", "cyberpunk 2"]},
    "baldur's gate 3":      {"tier": "A", "freq": 58, "aliases": ["baldur's gate", "bg3"]},
    "the witcher":          {"tier": "A", "freq": 58, "aliases": ["witcher 4", "witcher", "ведьмак"]},
    "god of war":           {"tier": "A", "freq": 53, "aliases": ["god of war ragnarok"]},
    "spider-man":           {"tier": "A", "freq": 51, "aliases": ["marvel's spider-man", "spider-man 2"]},
    "resident evil":        {"tier": "A", "freq": 62, "aliases": ["re9", "resident evil 9"]},
    "final fantasy":        {"tier": "A", "freq": 55, "aliases": ["final fantasy xvi", "final fantasy 16", "final fantasy 7", "ff7", "ff16"]},
    "red dead redemption":  {"tier": "A", "freq": 46, "aliases": ["red dead", "rdr", "rdr2"]},
    "the last of us":       {"tier": "A", "freq": 51, "aliases": ["tlou", "last of us"]},
    "death stranding 2":    {"tier": "A", "freq": 49, "aliases": ["death stranding"]},
    "metal gear solid":     {"tier": "A", "freq": 43, "aliases": ["mgs", "metal gear", "metal gear solid delta", "mgs delta"]},
    "silent hill":          {"tier": "A", "freq": 40, "aliases": ["silent hill 2", "silent hill f"]},
    "mass effect":          {"tier": "A", "freq": 42, "aliases": ["mass effect 5"]},
    "diablo":               {"tier": "A", "freq": 62, "aliases": ["diablo 4", "diablo iv"]},
    "hollow knight silksong": {"tier": "A", "freq": 46, "aliases": ["silksong", "hollow knight"]},
    "black myth wukong":    {"tier": "A", "freq": 63, "aliases": ["black myth", "wukong"]},
    "monster hunter wilds":  {"tier": "A", "freq": 58, "aliases": ["monster hunter", "mh wilds"]},
    "nintendo switch 2":    {"tier": "A", "freq": 82, "aliases": ["switch 2", "nintendo switch successor"]},
    "doom the dark ages":   {"tier": "A", "freq": 51, "aliases": ["doom dark ages"]},
    "marvel rivals":        {"tier": "A", "freq": 53, "aliases": []},
    "civilization 7":       {"tier": "A", "freq": 43, "aliases": ["civ 7", "civ vii", "civilization vii"]},
    "metroid prime 4":      {"tier": "A", "freq": 46, "aliases": ["metroid prime 4 beyond", "metroid prime"]},
    "pokemon":              {"tier": "A", "freq": 76, "aliases": ["pokémon", "pokemon legends"]},
    "mario":                {"tier": "A", "freq": 73, "aliases": ["super mario", "mario kart"]},
    "assassin's creed":     {"tier": "A", "freq": 51, "aliases": ["assassins creed", "ac shadows", "ac mirage"]},

    # === B-TIER: популярные, но без мега-хайпа ===
    "starfield":            {"tier": "B", "freq": 35, "aliases": []},
    "halo":                 {"tier": "B", "freq": 32, "aliases": ["halo infinite"]},
    "dragon age":           {"tier": "B", "freq": 40, "aliases": ["dragon age veilguard", "dragon age dreadwolf"]},
    "horizon":              {"tier": "B", "freq": 37, "aliases": ["horizon forbidden west", "horizon zero dawn"]},
    "overwatch":            {"tier": "B", "freq": 52, "aliases": ["overwatch 2"]},
    "valorant":             {"tier": "B", "freq": 78, "aliases": []},
    "league of legends":    {"tier": "B", "freq": 87, "aliases": ["lol", "лига легенд"]},
    "dota 2":               {"tier": "B", "freq": 84, "aliases": ["dota", "дота"]},
    "counter-strike":       {"tier": "B", "freq": 99, "aliases": ["cs2", "csgo", "cs:go", "counter strike"]},
    "escape from tarkov":   {"tier": "B", "freq": 51, "aliases": ["tarkov", "тарков"]},
    "genshin impact":       {"tier": "B", "freq": 66, "aliases": ["genshin", "геншин"]},
    "destiny 2":            {"tier": "B", "freq": 43, "aliases": ["destiny"]},
    "apex legends":         {"tier": "B", "freq": 68, "aliases": ["apex"]},
    "helldivers 2":         {"tier": "B", "freq": 44, "aliases": ["helldivers"]},
    "palworld":             {"tier": "B", "freq": 35, "aliases": []},
    "alan wake":            {"tier": "B", "freq": 28, "aliases": ["alan wake 2"]},
    "uncharted":            {"tier": "B", "freq": 26, "aliases": []},
    "fable":                {"tier": "B", "freq": 37, "aliases": ["fable reboot"]},
    "far cry":              {"tier": "B", "freq": 28, "aliases": []},
    "ghost of tsushima":    {"tier": "B", "freq": 35, "aliases": []},
    "world of warcraft":    {"tier": "B", "freq": 73, "aliases": ["wow"]},
    "honkai star rail":     {"tier": "B", "freq": 62, "aliases": ["honkai", "хонкай"]},
    "stellar blade":        {"tier": "B", "freq": 32, "aliases": []},
    "batman arkham":        {"tier": "B", "freq": 32, "aliases": ["batman"]},
    "indiana jones":        {"tier": "B", "freq": 45, "aliases": ["indiana jones great circle"]},
    "bioshock":             {"tier": "B", "freq": 23, "aliases": ["bioshock 4"]},
    "doom":                 {"tier": "B", "freq": 40, "aliases": []},
    "pubg":                 {"tier": "B", "freq": 87, "aliases": []},
    "avowed":               {"tier": "B", "freq": 37, "aliases": []},
    "wolverine":            {"tier": "B", "freq": 35, "aliases": ["marvel's wolverine"]},
    "judas":                {"tier": "B", "freq": 28, "aliases": []},

    # === B-TIER: российские/СНГ проекты ===
    "atomic heart":         {"tier": "B", "freq": 45, "aliases": ["атомное сердце"]},
    "pathfinder":           {"tier": "C", "freq": 28, "aliases": ["pathfinder wrath of the righteous", "pathfinder wotr"]},
    "warhammer 40k rogue trader": {"tier": "C", "freq": 23, "aliases": ["rogue trader"]},
    "replaced":             {"tier": "C", "freq": 20, "aliases": []},
    "смута":                {"tier": "C", "freq": 18, "aliases": ["smuta"]},
    "atom rpg":             {"tier": "C", "freq": 15, "aliases": []},
    "tiny bunny":           {"tier": "C", "freq": 17, "aliases": ["зайчик"]},
    "война миров сибирь":   {"tier": "C", "freq": 12, "aliases": ["war of the worlds siberia"]},

    # === C-TIER: нишевые, но с лояльной аудиторией ===
    "stardew valley":       {"tier": "C", "freq": 52, "aliases": []},
    "terraria":             {"tier": "C", "freq": 46, "aliases": []},
    "among us":             {"tier": "C", "freq": 23, "aliases": []},
    "fall guys":            {"tier": "C", "freq": 17, "aliases": []},
    "lethal company":       {"tier": "C", "freq": 28, "aliases": []},
    "factorio":             {"tier": "C", "freq": 40, "aliases": []},
    "cities skylines":      {"tier": "C", "freq": 32, "aliases": ["cities skylines 2"]},
    "the sims":             {"tier": "C", "freq": 35, "aliases": ["sims 5", "sims 4"]},
    "civilization":         {"tier": "C", "freq": 28, "aliases": ["sid meier's civilization"]},
    "age of empires":       {"tier": "C", "freq": 23, "aliases": ["aoe"]},
    "starcraft":            {"tier": "C", "freq": 17, "aliases": []},
    "tekken":               {"tier": "C", "freq": 35, "aliases": ["tekken 8"]},
    "street fighter":       {"tier": "C", "freq": 34, "aliases": ["street fighter 6", "sf6"]},
    "mortal kombat":        {"tier": "C", "freq": 35, "aliases": ["mk1"]},
    "persona":              {"tier": "C", "freq": 32, "aliases": ["persona 6", "persona 5"]},
    "kingdom hearts":       {"tier": "C", "freq": 20, "aliases": []},
    "dark souls":           {"tier": "C", "freq": 37, "aliases": []},
    "bloodborne":           {"tier": "C", "freq": 40, "aliases": []},
    "sekiro":               {"tier": "C", "freq": 28, "aliases": []},
    "armored core":         {"tier": "C", "freq": 20, "aliases": ["armored core 6"]},
    "skull and bones":      {"tier": "C", "freq": 5, "aliases": []},
    "zenless zone zero":    {"tier": "C", "freq": 55, "aliases": ["zzz"]},
    "perfect dark":         {"tier": "C", "freq": 23, "aliases": []},
}

STUDIO_ENTITIES = {
    # S-tier studios
    "rockstar games":       {"tier": "S", "freq": 73, "aliases": ["rockstar"]},
    "nintendo":             {"tier": "S", "freq": 82, "aliases": []},
    "valve":                {"tier": "S", "freq": 66, "aliases": []},
    "sony":                 {"tier": "S", "freq": 78, "aliases": ["sony interactive", "playstation studios", "sie"]},
    "microsoft":            {"tier": "S", "freq": 82, "aliases": ["xbox game studios", "microsoft gaming"]},

    # A-tier studios
    "fromsoftware":         {"tier": "A", "freq": 51, "aliases": ["from software"]},
    "cd projekt red":       {"tier": "A", "freq": 46, "aliases": ["cd projekt", "cdpr"]},
    "naughty dog":          {"tier": "A", "freq": 43, "aliases": []},
    "larian":               {"tier": "A", "freq": 40, "aliases": ["larian studios"]},
    "kojima productions":   {"tier": "A", "freq": 46, "aliases": ["hideo kojima", "кодзима"]},
    "bethesda":             {"tier": "A", "freq": 51, "aliases": ["bethesda game studios", "bethesda softworks"]},
    "insomniac":            {"tier": "A", "freq": 37, "aliases": ["insomniac games"]},
    "santa monica studio":  {"tier": "A", "freq": 32, "aliases": ["santa monica"]},
    "remedy":               {"tier": "A", "freq": 28, "aliases": ["remedy entertainment"]},
    "blizzard":             {"tier": "A", "freq": 62, "aliases": ["blizzard entertainment"]},
    "ea":                   {"tier": "A", "freq": 66, "aliases": ["electronic arts"]},
    "epic games":           {"tier": "A", "freq": 62, "aliases": ["epic"]},
    "capcom":               {"tier": "A", "freq": 55, "aliases": []},

    # B-tier studios
    "activision":           {"tier": "B", "freq": 58, "aliases": ["activision blizzard"]},
    "ubisoft":              {"tier": "B", "freq": 51, "aliases": []},
    "riot games":           {"tier": "B", "freq": 62, "aliases": ["riot"]},
    "square enix":          {"tier": "B", "freq": 46, "aliases": []},
    "bioware":              {"tier": "B", "freq": 32, "aliases": []},
    "obsidian":             {"tier": "B", "freq": 28, "aliases": ["obsidian entertainment"]},
    "bungie":               {"tier": "B", "freq": 35, "aliases": []},
    "respawn":              {"tier": "B", "freq": 23, "aliases": ["respawn entertainment"]},
    "guerrilla":            {"tier": "B", "freq": 20, "aliases": ["guerrilla games"]},

    # B-tier studios — Russian/CIS
    "owlcat games":         {"tier": "B", "freq": 32, "aliases": ["owlcat", "оулкэт"]},
    "mundfish":             {"tier": "B", "freq": 40, "aliases": ["мандфиш"]},

    # C-tier studios — Russian/CIS
    "cyberia nova":         {"tier": "C", "freq": 15, "aliases": ["кибериа нова"]},
    "soviet games":         {"tier": "C", "freq": 12, "aliases": []},
    "trioskaz":             {"tier": "C", "freq": 8, "aliases": ["триосказ"]},

    # C-tier studios
    "konami":               {"tier": "C", "freq": 28, "aliases": []},
    "sega":                 {"tier": "C", "freq": 32, "aliases": []},
    "bandai namco":         {"tier": "C", "freq": 35, "aliases": ["bandai"]},
    "team cherry":          {"tier": "C", "freq": 23, "aliases": []},
    "supergiant":           {"tier": "C", "freq": 17, "aliases": ["supergiant games"]},
    "devolver":             {"tier": "C", "freq": 13, "aliases": ["devolver digital"]},
    "annapurna":            {"tier": "C", "freq": 8, "aliases": ["annapurna interactive"]},
    "paradox":              {"tier": "C", "freq": 23, "aliases": ["paradox interactive"]},
    "firaxis":              {"tier": "C", "freq": 20, "aliases": []},
    "id software":          {"tier": "C", "freq": 23, "aliases": []},
    "machine games":        {"tier": "C", "freq": 17, "aliases": ["machinegames"]},
    "arkane":               {"tier": "C", "freq": 13, "aliases": ["arkane studios"]},
    "rare":                 {"tier": "C", "freq": 5, "aliases": []},
    "mojang":               {"tier": "C", "freq": 55, "aliases": []},
    "hoyoverse":            {"tier": "C", "freq": 51, "aliases": ["mihoyo"]},
    "tencent":              {"tier": "C", "freq": 43, "aliases": []},
}

PLATFORM_ENTITIES = {
    "playstation":  {"tier": "A", "freq": 85, "aliases": ["ps5", "ps4", "ps6", "playstation 5", "playstation 6", "psvr2"]},
    "xbox":         {"tier": "A", "freq": 82, "aliases": ["xbox series x", "xbox series s", "xbox one"]},
    "nintendo switch": {"tier": "A", "freq": 80, "aliases": ["switch", "switch oled"]},
    "steam":        {"tier": "A", "freq": 90, "aliases": ["steam deck", "steam deck 2"]},
    "pc":           {"tier": "B", "freq": 73, "aliases": []},
    "epic games store": {"tier": "B", "freq": 55, "aliases": ["egs"]},
    "game pass":    {"tier": "B", "freq": 66, "aliases": ["xbox game pass", "pc game pass"]},
    "ps plus":      {"tier": "B", "freq": 62, "aliases": ["playstation plus"]},
    "vr":           {"tier": "C", "freq": 43, "aliases": ["meta quest", "meta quest 3", "oculus", "psvr"]},
}

# Tier -> viral boost (используется в viral_score.py)
TIER_BOOST = {"S": 30, "A": 15, "B": 8, "C": 3}

# --- Lookup index: строится при импорте для быстрого поиска ---

_LOOKUP = {}  # {"gta 6": {"name": "gta 6", "type": "game", "tier": "S", "freq": 100}, ...}


def _build_lookup():
    """Строит плоский индекс alias->entity для быстрого поиска в тексте."""
    for name, data in GAME_ENTITIES.items():
        entry = {"name": name, "type": "game", "tier": data["tier"], "freq": data["freq"]}
        _LOOKUP[name] = entry
        for alias in data.get("aliases", []):
            if alias and alias not in _LOOKUP:
                _LOOKUP[alias] = entry

    for name, data in STUDIO_ENTITIES.items():
        entry = {"name": name, "type": "studio", "tier": data["tier"], "freq": data["freq"]}
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
