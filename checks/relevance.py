GAMING_KEYWORDS = [
    "игра", "game", "геймер", "gamer", "релиз", "release",
    "патч", "patch", "dlc", "gameplay", "студия", "studio",
    "разработчик", "developer", "publisher", "издатель",
    "трейлер", "trailer", "steam", "xbox", "playstation",
    "nintendo", "esports", "киберспорт", "обновление", "update",
    "rpg", "fps", "mmorpg", "инди", "indie", "консоль", "console",
    "мультиплеер", "multiplayer", "сингплеер", "singleplayer",
]

NOISE_KEYWORDS = ["политика", "экономика", "нефть", "курс доллара", "выборы"]


def check_relevance(news: dict) -> dict:
    text = (news.get("title", "") + " " + news.get("description", "") + " " + news.get("plain_text", "")).lower()

    gaming_hits = sum(1 for kw in GAMING_KEYWORDS if kw in text)
    noise_hits = sum(1 for kw in NOISE_KEYWORDS if kw in text)

    score = min(100, gaming_hits * 10) - noise_hits * 20
    # Pass if enough gaming signal, even with some noise
    passes = gaming_hits >= 2 and (noise_hits == 0 or gaming_hits >= noise_hits * 3)
    return {
        "score": max(0, score),
        "gaming_hits": gaming_hits,
        "noise_hits": noise_hits,
        "pass": passes,
    }
