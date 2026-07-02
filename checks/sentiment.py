"""Sentiment analysis — без тяжёлых моделей, на словарях."""

# Русские сентимент-слова
POSITIVE_RU = [
    "отлично", "великолепно", "лучший", "лучшая", "рекорд",
    "победа", "успех", "хвалят", "прорыв",
    "идеальный", "замечательный", "топ", "супер",
    "бесплатно", "награда", "рост трафика", "рост позиций",
    "в топе выдачи", "восстановление трафика",
]

NEGATIVE_RU = [
    "провал", "разочарование", "ужасный", "худший", "худшая", "баги",
    "сломано", "критика", "скандал", "бойкот", "увольнения",
    "закрыли", "отменили", "проблемы",
    "мусор", "позор", "крах", "убытки", "иск", "обман",
    "падение трафика", "просадка позиций", "фильтр", "санкции",
    "деиндексация", "выпал из индекса",
]

POSITIVE_EN = [
    "excellent", "amazing", "best", "record",
    "award", "praised", "stunning", "breakthrough", "perfect", "top",
    "free", "incredible", "outstanding", "brilliant",
    "traffic growth", "ranking boost", "recovery",
]

NEGATIVE_EN = [
    "flop", "disappointing", "terrible", "worst", "bugs",
    "broken", "backlash", "controversy", "boycott", "layoffs",
    "canceled", "cancelled", "problems",
    "trash", "disaster", "lawsuit", "fraud", "scam",
    "traffic drop", "ranking drop", "penalty", "deindexed",
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
