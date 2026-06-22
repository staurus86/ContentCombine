"""SEO analysis for articles."""

import re


def analyze_seo(title: str, seo_title: str, seo_description: str, text: str, tags: list) -> dict:
    """Analyze article SEO quality.

    Returns dict with:
        score (0-100): overall SEO score
        checks: list of {name, status: 'pass'|'warn'|'fail', message}
    """
    title = (title or "").strip()
    seo_title = (seo_title or "").strip()
    seo_description = (seo_description or "").strip()
    text = (text or "").strip()
    if tags is None:
        tags = []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    checks = []

    # 1. Title length
    tlen = len(title)
    if 20 <= tlen <= 70:
        checks.append({"name": "Длина заголовка", "status": "pass",
                        "message": f"{tlen} симв. (рекомендуется 20-70)"})
    else:
        checks.append({"name": "Длина заголовка", "status": "warn",
                        "message": f"{tlen} симв. — рекомендуется 20-70"})

    # 2. SEO Title length
    stlen = len(seo_title)
    if not seo_title:
        checks.append({"name": "SEO Title", "status": "fail",
                        "message": "Отсутствует SEO Title"})
    elif 50 <= stlen <= 60:
        checks.append({"name": "SEO Title", "status": "pass",
                        "message": f"{stlen} симв. (идеально 50-60)"})
    elif 40 <= stlen <= 70:
        checks.append({"name": "SEO Title", "status": "warn",
                        "message": f"{stlen} симв. — лучше 50-60"})
    else:
        checks.append({"name": "SEO Title", "status": "fail",
                        "message": f"{stlen} симв. — нужно 50-60"})

    # 3. SEO Description length
    sdlen = len(seo_description)
    if not seo_description:
        checks.append({"name": "Meta Description", "status": "fail",
                        "message": "Отсутствует Meta Description"})
    elif 120 <= sdlen <= 155:
        checks.append({"name": "Meta Description", "status": "pass",
                        "message": f"{sdlen} симв. (идеально 120-155)"})
    elif 100 <= sdlen <= 160:
        checks.append({"name": "Meta Description", "status": "warn",
                        "message": f"{sdlen} симв. — лучше 120-155"})
    else:
        checks.append({"name": "Meta Description", "status": "fail",
                        "message": f"{sdlen} симв. — нужно 120-155"})

    # 4. Text length (word count)
    words = text.split()
    wcount = len(words)
    if wcount > 300:
        checks.append({"name": "Объём текста", "status": "pass",
                        "message": f"{wcount} слов (рекомендуется >300)"})
    elif wcount > 150:
        checks.append({"name": "Объём текста", "status": "warn",
                        "message": f"{wcount} слов — лучше >300"})
    else:
        checks.append({"name": "Объём текста", "status": "fail",
                        "message": f"{wcount} слов — слишком мало, нужно >300"})

    # 5. Keyword density — title words in text
    title_words = set(w.lower() for w in re.findall(r'[a-zA-Zа-яА-ЯёЁ]{3,}', title))
    text_lower = text.lower()
    found = sum(1 for w in title_words if w in text_lower)
    if len(title_words) == 0:
        checks.append({"name": "Ключевые слова", "status": "fail",
                        "message": "Не удалось извлечь ключевые слова из заголовка"})
    elif found >= 2:
        checks.append({"name": "Ключевые слова", "status": "pass",
                        "message": f"{found} из {len(title_words)} слов заголовка найдены в тексте"})
    else:
        checks.append({"name": "Ключевые слова", "status": "warn",
                        "message": f"Только {found} из {len(title_words)} слов заголовка в тексте"})

    # 6. Tags
    tag_count = len(tags)
    if tag_count >= 2:
        checks.append({"name": "Теги", "status": "pass",
                        "message": f"{tag_count} тегов"})
    elif tag_count == 1:
        checks.append({"name": "Теги", "status": "warn",
                        "message": "Только 1 тег — рекомендуется минимум 2"})
    else:
        checks.append({"name": "Теги", "status": "fail",
                        "message": "Нет тегов"})

    # 7. Readability — avg sentence length
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if sentences:
        avg_sent = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_sent < 25:
            checks.append({"name": "Читаемость", "status": "pass",
                            "message": f"Ср. длина предложения: {avg_sent:.0f} слов (хорошо <25)"})
        elif avg_sent < 35:
            checks.append({"name": "Читаемость", "status": "warn",
                            "message": f"Ср. длина предложения: {avg_sent:.0f} слов — лучше <25"})
        else:
            checks.append({"name": "Читаемость", "status": "fail",
                            "message": f"Ср. длина предложения: {avg_sent:.0f} слов — слишком длинные"})
    else:
        checks.append({"name": "Читаемость", "status": "fail",
                        "message": "Не удалось определить предложения"})

    # 8. First paragraph — strong lead
    first_period = text.find(".")
    if first_period > 20:
        checks.append({"name": "Лид (первый абзац)", "status": "pass",
                        "message": f"Сильный лид ({first_period} симв. до первой точки)"})
    elif first_period > 0:
        checks.append({"name": "Лид (первый абзац)", "status": "warn",
                        "message": f"Короткий лид ({first_period} симв.) — расширьте вступление"})
    else:
        checks.append({"name": "Лид (первый абзац)", "status": "fail",
                        "message": "Нет точки в тексте — проверьте структуру"})

    # 9. Subheadings — required for all articles (H2 mandatory per editorial policy)
    has_headings = bool(re.search(r'(^|\n)#{2,}\s|<h[23][^>]*>', text))
    if has_headings:
        checks.append({"name": "Подзаголовки (H2)", "status": "pass",
                        "message": "Найдены подзаголовки (## / h2 / h3)"})
    else:
        checks.append({"name": "Подзаголовки (H2)", "status": "fail",
                        "message": "Нет подзаголовков — обязателен минимум один <h2>"})

    # --- SEO 2.0 extended checks ---

    # 10. SEO Title differs from Title
    if seo_title and title:
        if seo_title.lower().strip() == title.lower().strip():
            checks.append({"name": "Уникальность SEO Title", "status": "warn",
                            "message": "SEO Title совпадает с заголовком — лучше адаптировать под поиск"})
        else:
            checks.append({"name": "Уникальность SEO Title", "status": "pass",
                            "message": "SEO Title отличается от заголовка"})

    # 11. Description contains keywords from title
    if seo_description and title_words:
        desc_lower = seo_description.lower()
        desc_kw = sum(1 for w in title_words if w in desc_lower)
        if desc_kw >= 2:
            checks.append({"name": "Ключевые в Description", "status": "pass",
                            "message": f"{desc_kw} ключевых слов из заголовка в Description"})
        elif desc_kw == 1:
            checks.append({"name": "Ключевые в Description", "status": "warn",
                            "message": "Только 1 ключевое слово в Description — добавьте ещё"})
        else:
            checks.append({"name": "Ключевые в Description", "status": "fail",
                            "message": "Ключевые слова заголовка отсутствуют в Description"})

    # 12. Paragraph structure
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n|\n', text) if p.strip()]
    if len(paragraphs) >= 3:
        checks.append({"name": "Структура абзацев", "status": "pass",
                        "message": f"{len(paragraphs)} абзацев — хорошая структура"})
    elif len(paragraphs) >= 2:
        checks.append({"name": "Структура абзацев", "status": "warn",
                        "message": f"Только {len(paragraphs)} абзаца — разбейте текст"})
    else:
        checks.append({"name": "Структура абзацев", "status": "fail",
                        "message": "Текст одним блоком — разбейте на абзацы"})

    # 13. Internal linking potential (URLs in text)
    urls_in_text = re.findall(r'https?://\S+', text)
    if urls_in_text:
        checks.append({"name": "Ссылки в тексте", "status": "pass",
                        "message": f"{len(urls_in_text)} ссылок в тексте"})
    elif wcount > 300:
        checks.append({"name": "Ссылки в тексте", "status": "warn",
                        "message": "Нет ссылок — добавьте внутренние/внешние ссылки"})

    # 14. Numbers / data in text (search engines prefer data-rich content)
    numbers = re.findall(r'\b\d{2,}\b', text)
    if len(numbers) >= 3:
        checks.append({"name": "Данные и цифры", "status": "pass",
                        "message": f"{len(numbers)} числовых значений — контент богат данными"})
    elif len(numbers) >= 1:
        checks.append({"name": "Данные и цифры", "status": "warn",
                        "message": f"Только {len(numbers)} чисел — добавьте факты и статистику"})

    # Calculate score
    weights = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
    total = len(checks)
    if total == 0:
        score = 0
    else:
        raw = sum(weights.get(c["status"], 0) for c in checks) / total * 100
        score = round(raw)

    return {"score": score, "checks": checks}
