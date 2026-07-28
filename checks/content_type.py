"""Класс материала: событие, разбор, вечнозелёный гайд, рекап, анонс.

Скоринг мерил плотность тематических слов, поэтому ежедневный обзор форума
(«Daily Search Forum Recap») набирал 100 из 100: он упоминает все горячие темы
разом. Вечнозелёный справочник («What are backlinks in SEO») — 97. Ни один из
них редактор в дайджест не брал.

Класс материала — отдельная ось от темы. Множитель применяется к total_score
в checks/pipeline.py, рекапы и анонсы дополнительно не попадают в ТОП.

Fails open: непонятный материал → analysis с множителем 1.0.
"""

import re

# Множители откалиброваны по 30 дням ленты (3 700 материалов, сегмент без TG и
# кейсов): средний скор analysis 49.6, recap 80.5, evergreen 65.8, event 57.1,
# announcement 52.9. Рекап опережает обычный материал на 31 балл — его и правим.
# Гайды редактор берёт вдвое чаще среднего (23.4% против 10.1%), поэтому их не
# штрафуем. Не обнуляем никого: рекап с эксклюзивом всё ещё может пройти в ленту,
# он лишь не должен её возглавлять.
TYPE_MULTIPLIER = {
    "event": 1.1,         # само событие: анонс, релиз, подтверждение, сбой
    "analysis": 1.0,      # разбор, мнение, исследование — база
    "evergreen": 1.0,     # справочник и обучающий гайд: спрос подтверждён данными
    "recap": 0.65,        # обзор чужих новостей: дайджест, роундап, подкаст-выпуск
    "announcement": 0.5,  # вебинар, конференция, вакансия, реклама своего продукта
}

# Классы, которым закрыт вход в ТОП и в первые позиции дайджеста.
LOW_VALUE_TYPES = {"recap", "announcement"}

_RECAP = re.compile(
    r"\b(recap|round-?up|digest|newsletter|weekly\s+(?:seo|search|ai|update)|week\s+in\s+review"
    r"|this\s+week\s+in|подкаст|дайджест|обзор\s+недели|итоги\s+(?:недели|месяца|дня)"
    r"|главное\s+за\s+(?:неделю|день|месяц)|что\s+было\s+на\s+неделе"
    # Форматы дайджестов Telegram-каналов: сами по себе это пересказ чужих новостей.
    r"|всё,?\s+что\s+нужно\s+знать\s+о\s+seo|новостной\s*(?:🥃)?\s*шот|подборка\s+новостей)\b",
    re.IGNORECASE)

_EVERGREEN = re.compile(
    r"(^what\s+(?:is|are)\b|^how\s+to\b|\bcomplete\s+guide\b|\bultimate\s+guide\b|\bbeginner'?s\s+guide\b"
    r"|\bstep-by-step\b|\bchecklist\b|\btutorial\b|\bbest\s+practices\b|\bexplained$"
    r"|^как\s+[а-яё]+(?:ть|ться)\b|^что\s+такое\b|^зачем\s+нужн|полное\s+руководство"
    r"|\bруководство\s+по\b|\bчеклист\b|\bинструкция\b|\bпошагов)",
    re.IGNORECASE)

_ANNOUNCEMENT = re.compile(
    r"(\bвебинар\b|\bконференци|\bмитап\b|\bmeetup\b|\bwebinar\b|\bregister\s+now\b"
    r"|\bищем\b|\bвакансия\b|\bhiring\b|\bwe'?re\s+hiring\b|\bрозыгрыш\b|\bскидк[аи]\b"
    r"|\bпромокод\b|\bкурс\s+стартует\b|\bнабор\s+на\s+курс\b|\bприглашаем\b"
    r"|\bедет\s+на\b|\bбудем\s+рады\s+видеть\b)",
    re.IGNORECASE)

_EVENT = re.compile(
    r"(\bconfirm(?:s|ed)?\b|\bannounce(?:s|d)?\b|\blaunch(?:es|ed)\b|\breleases?\b|\brolling\s+out\b"
    r"|\brolled\s+out\b|\bshutting\s+down\b|\bdeprecat|\bnow\s+live\b|\bleak(?:ed)?\b|\boutage\b"
    r"|подтвердил|анонсировал|выкатил|запустил|выпустил|представил|отключа|прекраща|закрывает"
    r"|обновил|сбой|утечк|официально)",
    re.IGNORECASE)


def classify_content(news: dict) -> dict:
    """Возвращает {"type", "multiplier", "low_value"} по заголовку и началу текста."""
    try:
        title = (news.get("title") or "").strip()
        body = (news.get("plain_text") or news.get("description") or "")[:400]
        both = f"{title} {body}"

        if _RECAP.search(title) or _RECAP.search(body[:160]):
            ctype = "recap"
        elif _ANNOUNCEMENT.search(both):
            ctype = "announcement"
        elif _EVERGREEN.search(title):
            ctype = "evergreen"
        elif _EVENT.search(title):
            ctype = "event"
        else:
            ctype = "analysis"
    except Exception:
        ctype = "analysis"

    return {
        "type": ctype,
        "multiplier": TYPE_MULTIPLIER.get(ctype, 1.0),
        "low_value": ctype in LOW_VALUE_TYPES,
    }
