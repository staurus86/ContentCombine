def check_quality(news: dict) -> dict:
    """Градуированная оценка качества: балл НАБИРАЕТСЯ от 0 за реальные сигналы
    (глубина текста, нормальный заголовок, description, структура), а не вычитается
    из 100 — иначе почти всё получало ~100 и качество не различало материалы."""
    issues = []
    plain_text = news.get("plain_text", "") or ""
    description = news.get("description", "") or ""
    title = news.get("title", "") or ""
    h1 = news.get("h1", "") or ""
    image_count = news.get("image_count", 0) or 0
    best_text = plain_text or description
    text_len = len(best_text)

    score = 0

    # Глубина текста — до 45
    if text_len >= 1500:
        score += 45
    elif text_len >= 800:
        score += 40
    elif text_len >= 400:
        score += 30
    elif text_len >= 150:
        score += 18
    elif text_len > 0:
        score += 7
    else:
        issues.append("Нет текста")

    # Заголовок — до 25 (оптимум 30-90 символов)
    tl = len(title)
    if 30 <= tl <= 90:
        score += 25
    elif (20 <= tl < 30) or (90 < tl <= 130):
        score += 18
    elif tl >= 10:
        score += 8
    else:
        issues.append("Заголовок слишком короткий")

    # Description — до 15
    if len(description) >= 80:
        score += 15
    elif len(description) >= 30:
        score += 9
    else:
        issues.append("Нет/короткий description")

    # Структурные сигналы — до 15
    if h1 and h1 != title:
        score += 6
    if image_count and image_count > 0:
        score += 4
    if best_text.count("\n") >= 3 or text_len >= 1200:
        score += 5

    # Штраф за кликбейт
    clickbait = ["ШОК", "НЕВЕРОЯТНО", "ВЫ НЕ ПОВЕРИТЕ", "!!!", "???"]
    if any(c in title.upper() for c in clickbait):
        issues.append("Возможный кликбейт")
        score -= 15

    score = max(0, min(100, score))
    return {"score": score, "issues": issues, "pass": score >= 40}
