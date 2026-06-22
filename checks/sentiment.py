"""Sentiment analysis — без тяжёлых моделей, на словарях."""

# Русские сентимент-слова
POSITIVE_RU = [
    "отлично", "великолепно", "шедевр", "лучший", "лучшая", "рекорд",
    "победа", "успех", "хвалят", "восторг", "потрясающий", "прорыв",
    "идеальный", "замечательный", "топ", "супер", "феноменальный",
    "бесплатно", "раздача", "награда", "goty", "платина",
]

NEGATIVE_RU = [
    "провал", "разочарование", "ужасный", "худший", "худшая", "баги",
    "вылеты", "сломано", "критика", "скандал", "бойкот", "увольнения",
    "закрыли", "отменили", "перенос", "проблемы", "неоптимизировано",
    "мусор", "треш", "позор", "крах", "убытки", "иск", "обман",
]

POSITIVE_EN = [
    "excellent", "amazing", "masterpiece", "best", "record", "goty",
    "award", "praised", "stunning", "breakthrough", "perfect", "top",
    "phenomenal", "free", "giveaway", "platinum", "incredible",
    "outstanding", "brilliant", "must-play",
]

NEGATIVE_EN = [
    "flop", "disappointing", "terrible", "worst", "bugs", "crashes",
    "broken", "backlash", "controversy", "boycott", "layoffs",
    "canceled", "cancelled", "delayed", "problems", "unoptimized",
    "trash", "disaster", "lawsuit", "fraud", "scam", "refund",
    "overwhelmingly negative", "mostly negative",
]

# Pre-concatenated lists (built once at import)
_ALL_POSITIVE = POSITIVE_RU + POSITIVE_EN
_ALL_NEGATIVE = NEGATIVE_RU + NEGATIVE_EN


def analyze_sentiment(news: dict) -> dict:
    """Анализирует тональность новости. Возвращает score от -1 до +1."""
    plain = news.get("plain_text", "") or news.get("description", "") or ""
    text = (news.get("title", "") + " " + plain).lower()

    pos_hits = sum(1 for w in _ALL_POSITIVE if w in text)
    neg_hits = sum(1 for w in _ALL_NEGATIVE if w in text)

    total = pos_hits + neg_hits
    if total == 0:
        return {"score": 0.0, "label": "neutral", "positive": 0, "negative": 0}

    score = (pos_hits - neg_hits) / total  # -1.0 to +1.0

    if score > 0.2:
        label = "positive"
    elif score < -0.2:
        label = "negative"
    else:
        label = "neutral"

    return {
        "score": round(score, 2),
        "label": label,
        "positive": pos_hits,
        "negative": neg_hits,
    }
