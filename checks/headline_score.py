"""Headline scoring — оценка кликабельности заголовка по SEO/медиа-паттернам."""

import re

HEADLINE_PATTERNS = {
    "number": {
        "pattern": r'\d+',
        "bonus": 10,
        "label": "Number in title",
    },
    "question": {
        "pattern": r'\?$',
        "bonus": 8,
        "label": "Question headline",
    },
    "exclusive": {
        "pattern": r'эксклюзивно|exclusive|only on',
        "bonus": 15,
        "label": "Exclusive",
    },
    "official": {
        "pattern": r'официально|official|подтвердили|confirmed',
        "bonus": 12,
        "label": "Official/Confirmed",
    },
    "how_to": {
        "pattern": r'^как |^how to|^гайд|^guide',
        "bonus": 5,
        "label": "How-to/Guide",
    },
    "vs": {
        "pattern": r' vs\.? | против ',
        "bonus": 10,
        "label": "Versus/Comparison",
    },
    "breaking": {
        "pattern": r'срочно|breaking|молния',
        "bonus": 20,
        "label": "Breaking news",
    },
    "first": {
        "pattern": r'впервые|first ever|first look|первый взгляд|мировая премьера|world premiere',
        "bonus": 15,
        "label": "First/Premiere",
    },
    "list": {
        "pattern": r'^\d+ (лучших|причин|игр|вещей|способов|tips|best|reasons|games|things)',
        "bonus": 8,
        "label": "List format",
    },
    "negative_strong": {
        "pattern": r'провал|худш|disaster|worst|flop|ужас',
        "bonus": 12,
        "label": "Strong negative",
    },
    "emotional": {
        "pattern": r'шок|невероятно|incredible|amazing|insane|stunning|epic',
        "bonus": 7,
        "label": "Emotional word",
    },
    "urgency": {
        "pattern": r'прямо сейчас|now available|уже доступн|out now|уже можно',
        "bonus": 10,
        "label": "Urgency",
    },
    # Russian-specific patterns
    "leak_ru": {
        "pattern": r'утечка|слив|рассекретили|слили',
        "bonus": 12,
        "label": "Утечка",
    },
    "scandal_ru": {
        "pattern": r'скандал|разгром|провал|позор',
        "bonus": 12,
        "label": "Скандал",
    },
    "free_ru": {
        "pattern": r'бесплатно|раздают|даром|free-to-play',
        "bonus": 10,
        "label": "Бесплатно",
    },
    "russian_game": {
        "pattern": r'русск|отечественн|российск|от\s+русских',
        "bonus": 8,
        "label": "Русская игра",
    },
    "release_ru": {
        "pattern": r'вышла|релиз|уже доступн|стартовал',
        "bonus": 8,
        "label": "Релиз",
    },
    "record_ru": {
        "pattern": r'рекорд|побила|миллион\s+игроков|пик\s+онлайн',
        "bonus": 10,
        "label": "Рекорд",
    },
    "ban_ru": {
        "pattern": r'забанили|заблокировали|запрет|удалили\s+из',
        "bonus": 10,
        "label": "Бан/Запрет",
    },
}

# Pre-compile all patterns at import time
_COMPILED_PATTERNS = {
    pat_id: (re.compile(pat_info["pattern"]), pat_info["bonus"], pat_info["label"])
    for pat_id, pat_info in HEADLINE_PATTERNS.items()
}

# Penalties
HEADLINE_PENALTIES = {
    "too_short": {
        "check": lambda t: len(t) < 25,
        "penalty": -15,
        "label": "Too short (<25 chars)",
    },
    "too_long": {
        "check": lambda t: len(t) > 120,
        "penalty": -10,
        "label": "Too long (>120 chars)",
    },
    "all_caps_words": {
        "check": lambda t: sum(1 for w in t.split() if w.isupper() and len(w) > 2) >= 3,
        "penalty": -10,
        "label": "Too many CAPS words",
    },
    "clickbait_markers": {
        "check": lambda t: any(m in t.upper() for m in ["ВЫ НЕ ПОВЕРИТЕ", "YOU WON'T BELIEVE", "!!!"]),
        "penalty": -15,
        "label": "Clickbait markers",
    },
}


def headline_score(news: dict) -> dict:
    """Оценивает кликабельность заголовка. Возвращает score 0-100."""
    title = news.get("title", "")
    title_lower = title.lower()

    score = 40  # base score
    triggers = []

    # Patterns (bonuses) — uses pre-compiled regex
    for pat_id, (compiled_re, bonus, label) in _COMPILED_PATTERNS.items():
        if compiled_re.search(title_lower):
            score += bonus
            triggers.append({
                "id": pat_id,
                "label": label,
                "bonus": bonus,
            })

    # Penalties
    for pen_id, pen_info in HEADLINE_PENALTIES.items():
        if pen_info["check"](title):
            score += pen_info["penalty"]
            triggers.append({
                "id": pen_id,
                "label": pen_info["label"],
                "bonus": pen_info["penalty"],
            })

    # Length bonus — optimal 40-80 chars
    tlen = len(title)
    if 40 <= tlen <= 80:
        score += 5
        triggers.append({"id": "optimal_length", "label": f"Good length ({tlen})", "bonus": 5})

    score = max(0, min(100, score))

    if score >= 70:
        level = "high"
    elif score >= 50:
        level = "medium"
    else:
        level = "low"

    return {
        "score": score,
        "level": level,
        "triggers": triggers,
        "title_length": tlen,
    }
