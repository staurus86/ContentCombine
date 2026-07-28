"""Скоринг постов Telegram-каналов.

Общие чеки на TG не работают: у поста нет заголовка, description и длинного
текста, поэтому quality, relevance и viral дают почти ноль. Скор всех постов
лёг в диапазон 12–25 и перестал различать их между собой — отбор в дайджест
стал случайным, и в канал уходили «Проверка сразу на проде😅», «Ищем middle
SEO-специалиста» и «Доброе утро! Кофе в пятницу особенно вкусный».

Модуль добавляет к скору TG-поста поправку по сигналам, которые у поста есть:
служебный он или содержательный, есть ли ссылка на первоисточник, разбор это
или одна строка. Fails open: ошибка → нулевая поправка.
"""

import re

# Служебные посты канала: рекрутинг, реклама мероприятий, дежурные приветствия,
# самопиар. В новостном дайджесте им не место.
_JUNK_PATTERNS = [
    (re.compile(r"\bищем\b|\bвакансия\b|\bв команду\b|\bрезюме\b|\bhiring\b|\bищу\s+(?:спеца|специалиста)\b", re.I), -35, "вакансия"),
    (re.compile(r"\bвебинар\b|\bконференци|\bмитап\b|\bmeetup\b|\bприглашаем\b|\bедет\s+на\b|\bстенд\b|\bбудем\s+рады\s+видеть\b", re.I), -30, "мероприятие"),
    (re.compile(r"\bпромокод\b|\bскидк[аи]\b|\bрозыгрыш\b|\bконкурс\b|\bдарим\b|\bтариф\b|\bподписк[аиу]\s+на\b", re.I), -30, "реклама"),
    (re.compile(r"^\s*(?:доброе\s+утро|добрый\s+день|всем\s+привет|с\s+пятницей|с\s+праздником|доброго)", re.I), -35, "дежурный пост"),
    (re.compile(r"\bпоздравляем\b|\bс\s+днём\s+рождения\b|\bмем\b|\bмемас\b|\bпятничн", re.I), -25, "развлекательное"),
    (re.compile(r"\bопрос\b\s*[:—-]|\bа\s+как\s+у\s+вас\b|\bчто\s+думаете\b\s*\?$", re.I), -15, "опрос"),
    (re.compile(r"\bреклама\b|\berid\b|\bна\s+правах\s+рекламы\b", re.I), -35, "маркировка рекламы"),
]

# Содержательные сигналы.
_LINK_RE = re.compile(r"https?://")
_NUMBER_RE = re.compile(r"\b\d{2,}\s*(?:%|процент|раз|позиц|запрос|сайт|тыс|млн)|\b\d+[.,]\d+\b")
_SUBSTANCE_RE = re.compile(
    r"\bкейс\b|\bразбор\b|\bтест\b|\bэксперимент\b|\bапдейт\b|\bобновлени|\bисследовани"
    r"|\bподтвердил\b|\bвыкатил|\bзапустил|\bсломал|\bупал[аи]?\b|\bвырос|\bинструкц|\bчеклист\b",
    re.I)
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def score_tg_post(news: dict) -> dict:
    """Возвращает {"delta", "flags", "junk"} — поправку к total_score для TG-поста."""
    try:
        text = (news.get("plain_text") or news.get("description") or news.get("title") or "")
        head = text[:400]
        delta, flags, junk = 0, [], False

        for rx, penalty, label in _JUNK_PATTERNS:
            if rx.search(head):
                delta += penalty
                flags.append(label)
                junk = True

        n = len(text)
        if n >= 900:
            delta += 12
            flags.append("развёрнутый пост")
        elif n >= 400:
            delta += 7
        elif n < 120:
            delta -= 10
            flags.append("одна строка")

        if _LINK_RE.search(text):
            delta += 8
            flags.append("ссылка на источник")
        if _SUBSTANCE_RE.search(head):
            delta += 10
            flags.append("разбор/событие")
        if _NUMBER_RE.search(head):
            delta += 6
            flags.append("цифры")

        # Пост из сплошных эмодзи и восклицаний — почти всегда служебный.
        if n and len(_EMOJI_RE.findall(text)) / max(n / 100, 1) > 4:
            delta -= 8
            flags.append("эмодзи-спам")

        return {"delta": max(-40, min(30, delta)), "flags": flags, "junk": junk}
    except Exception:
        return {"delta": 0, "flags": [], "junk": False}
