def check_quality(news: dict) -> dict:
    issues = []
    score = 100

    plain_text = news.get("plain_text", "")
    description = news.get("description", "")
    title = news.get("title", "")

    # Используем наибольший доступный текст для оценки длины
    best_text = plain_text or description or ""
    text_len = len(best_text)

    if text_len == 0:
        issues.append("Нет текста")
        score -= 50
    elif text_len < 100:
        issues.append("Текст слишком короткий")
        score -= 40
    elif text_len < 150:
        issues.append("Текст короткий")
        score -= 20

    if len(title) < 20:
        issues.append("Заголовок слишком короткий")
        score -= 20

    clickbait = ["ШОК", "НЕВЕРОЯТНО", "ВЫ НЕ ПОВЕРИТЕ", "!!!", "???"]
    if any(c in title.upper() for c in clickbait):
        issues.append("Возможный кликбейт")
        score -= 15

    if not description:
        issues.append("Нет description")
        score -= 10

    return {"score": max(0, score), "issues": issues, "pass": score >= 40}
