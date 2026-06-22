"""Named Entity Recognition — оптимизированный словарный подход.

Использует предкомпилированные множества и regex для быстрого поиска.
"""

import re
from functools import lru_cache

STUDIOS = [
    "rockstar", "rockstar games", "valve", "blizzard", "activision", "ubisoft",
    "ea", "electronic arts", "bethesda", "cd projekt red", "cd projekt",
    "epic games", "riot games", "bungie", "bioware", "obsidian", "insomniac",
    "naughty dog", "santa monica", "guerrilla", "playground games",
    "from software", "fromsoftware", "capcom", "konami", "sega", "bandai namco",
    "square enix", "nintendo", "sony", "microsoft", "xbox game studios",
    "team cherry", "supergiant", "devolver", "annapurna", "larian",
    "respawn", "infinity ward", "treyarch", "sledgehammer", "rare",
    "mojang", "mihoyo", "hoyoverse", "tencent", "netease", "krafton",
    "innersloth", "re-logic", "coffee stain", "paradox", "firaxis",
    "id software", "machine games", "arkane", "remedy", "sam lake",
    "kojima productions", "hideo kojima", "neil druckmann", "todd howard",
    "phil spencer", "shuhei yoshida", "tim sweeney", "gabe newell",
    "geoff keighley", "jason schreier",
]

GAMES = [
    "gta 6", "gta vi", "grand theft auto 6", "grand theft auto vi",
    "elder scrolls 6", "elder scrolls vi", "half-life 3", "half-life alyx",
    "call of duty", "modern warfare", "warzone", "black ops",
    "zelda", "tears of the kingdom", "breath of the wild",
    "pokemon", "mario", "mario kart", "super mario", "metroid",
    "batman arkham", "spider-man", "god of war", "god of war ragnarok",
    "horizon", "horizon forbidden west", "horizon zero dawn",
    "red dead redemption", "red dead", "cyberpunk 2077", "cyberpunk",
    "elden ring", "baldur's gate 3", "baldur's gate", "starfield",
    "diablo", "diablo 4", "diablo iv", "world of warcraft", "wow",
    "final fantasy", "final fantasy xvi", "final fantasy 16",
    "resident evil", "fortnite", "minecraft", "the witcher",
    "mass effect", "dragon age", "halo", "halo infinite",
    "overwatch", "overwatch 2", "valorant", "league of legends", "lol",
    "dota 2", "counter-strike", "cs2", "csgo",
    "destiny 2", "the last of us", "tlou", "uncharted",
    "assassin's creed", "far cry", "ghost of tsushima",
    "death stranding", "metal gear solid", "mgs", "silent hill",
    "alan wake", "control", "hollow knight", "silksong",
    "palworld", "lethal company", "helldivers", "helldivers 2",
    "stellar blade", "black myth wukong", "skull and bones",
    "starcraft", "age of empires", "civilization",
    "the sims", "sims 5", "cities skylines", "factorio",
    "stardew valley", "terraria", "among us", "fall guys",
    "apex legends", "pubg", "escape from tarkov", "tarkov",
    "genshin impact", "honkai star rail", "zenless zone zero",
    "armored core", "tekken", "street fighter", "mortal kombat",
]

PLATFORMS = [
    "ps5", "ps4", "playstation 5", "playstation 4", "playstation 6", "ps6",
    "xbox series x", "xbox series s", "xbox one", "xbox",
    "nintendo switch", "switch 2", "switch oled",
    "steam", "steam deck", "epic games store", "egs",
    "pc", "mac", "ios", "android", "mobile",
    "vr", "psvr2", "meta quest", "oculus",
]

# Pre-build: sorted by length (longest first) for greedy matching
_SORTED_STUDIOS = sorted(STUDIOS, key=len, reverse=True)
_SORTED_GAMES = sorted(GAMES, key=len, reverse=True)
_SORTED_PLATFORMS = sorted(PLATFORMS, key=len, reverse=True)

# Short keys (<=3 chars) need word boundary regex to avoid false matches
_SHORT_THRESHOLD = 3
_SHORT_PATTERNS = {}
for _list in (STUDIOS, GAMES, PLATFORMS):
    for _item in _list:
        if len(_item) <= _SHORT_THRESHOLD:
            _SHORT_PATTERNS[_item] = re.compile(r'\b' + re.escape(_item) + r'\b')

EVENT_KWS = {
    "The Game Awards": ["the game awards", "tga"],
    "E3": ["e3"],
    "Gamescom": ["gamescom"],
    "GDC": ["gdc"],
    "Nintendo Direct": ["nintendo direct"],
    "Xbox Showcase": ["xbox showcase"],
    "PlayStation Showcase": ["playstation showcase", "state of play"],
    "Summer Game Fest": ["summer game fest", "sgf"],
    "Tokyo Game Show": ["tokyo game show", "tgs"],
}

_NUMBER_RE = re.compile(r'\b(\d[\d\s,.]*\d)\b|\b(\d+)\b')
_SKIP_YEARS = {2024, 2025, 2026, 2027}


def _find_in_list(text: str, sorted_list: list) -> list:
    """Finds all matches from sorted_list in text, using word-boundary for short keys."""
    found = []
    for item in sorted_list:
        if len(item) <= _SHORT_THRESHOLD:
            pat = _SHORT_PATTERNS.get(item)
            if pat and pat.search(text):
                found.append(item)
        elif item in text:
            found.append(item)
    return found


@lru_cache(maxsize=256)
def _extract_cached(text_lower: str) -> tuple:
    """Cached entity extraction. Returns tuple for hashability."""
    found_studios = _find_in_list(text_lower, _SORTED_STUDIOS)
    found_games = _find_in_list(text_lower, _SORTED_GAMES)
    found_platforms = _find_in_list(text_lower, _SORTED_PLATFORMS)

    numbers = []
    for m in _NUMBER_RE.finditer(text_lower):
        raw = (m.group(1) or m.group(2)).replace(" ", "").replace(",", "").replace(".", "")
        try:
            n = int(raw)
            if n >= 10 and n not in _SKIP_YEARS:
                numbers.append(n)
        except ValueError:
            pass
    numbers = sorted(set(numbers), reverse=True)[:5]

    events = []
    for event_name, kws in EVENT_KWS.items():
        if any(kw in text_lower for kw in kws):
            events.append(event_name)

    return (
        tuple(found_studios[:10]),
        tuple(found_games[:10]),
        tuple(found_platforms[:5]),
        tuple(numbers),
        tuple(events),
        len(found_studios) + len(found_games) + len(found_platforms),
    )


def extract_entities(news: dict) -> dict:
    """Извлекает именованные сущности из новости. Результаты кешируются (LRU 256)."""
    title = news.get("title", "").lower()
    plain = news.get("plain_text", "") or news.get("description", "") or ""
    text = (title + " " + plain).lower()

    studios, games, platforms, numbers, events, total = _extract_cached(text)

    return {
        "studios": list(studios),
        "games": list(games),
        "platforms": list(platforms),
        "numbers": list(numbers),
        "events": list(events),
        "total_entities": total,
    }
