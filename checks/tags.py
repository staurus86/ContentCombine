"""Автоматические теги по таксономии."""

TAXONOMY = {
    "industry": {
        "label": "Industry",
        "keywords": [
            "увольнения", "сокращения", "layoff", "layoffs", "купила", "acquisition",
            "merger", "слияние", "сделка", "deal", "revenue", "выручка", "финансы",
            "studio closed", "закрыли студию", "IPO", "инвестиции", "investment",
            "partnership", "партнёрство",
        ],
    },
    "release": {
        "label": "Release",
        "keywords": [
            "релиз", "release", "вышла", "launched", "выходит", "дата выхода",
            "release date", "coming out", "launch day", "day one", "gold",
            "ушла на золото", "gone gold", "available now",
        ],
    },
    "update": {
        "label": "Update/Patch",
        "keywords": [
            "патч", "patch", "обновление", "update", "hotfix", "хотфикс",
            "dlc", "дополнение", "expansion", "season pass", "сезонный пропуск",
            "patch notes", "changelog",
        ],
    },
    "esports": {
        "label": "Esports",
        "keywords": [
            "турнир", "tournament", "чемпионат", "championship", "киберспорт",
            "esports", "esport", "команда", "team", "матч", "match",
            "лига", "league", "призовой", "prize pool", "mvp",
        ],
    },
    "hardware": {
        "label": "Hardware",
        "keywords": [
            "консоль", "console", "видеокарта", "gpu", "процессор", "cpu",
            "ps5", "ps6", "xbox", "switch", "steam deck", "геймпад",
            "controller", "vr", "headset", "периферия",
        ],
    },
    "controversy": {
        "label": "Controversy",
        "keywords": [
            "скандал", "controversy", "иск", "lawsuit", "бойкот", "boycott",
            "критика", "backlash", "обвинения", "allegations", "ban",
            "заблокировали", "цензура", "censorship",
        ],
    },
    "rumor": {
        "label": "Rumor/Leak",
        "keywords": [
            "слух", "rumor", "rumour", "утечка", "leak", "leaked",
            "инсайдер", "insider", "по слухам", "allegedly", "datamine",
            "datamined", "предположительно",
        ],
    },
    "review": {
        "label": "Review",
        "keywords": [
            "обзор", "review", "рецензия", "оценка", "metacritic",
            "score", "rating", "отзывы", "reviews", "критики",
            "opencritic", "impressions", "превью", "preview",
        ],
    },
    "announcement": {
        "label": "Announcement",
        "keywords": [
            "анонс", "announce", "announced", "revealed", "тизер", "teaser",
            "трейлер", "trailer", "показали", "reveal", "first look",
            "world premiere",
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
