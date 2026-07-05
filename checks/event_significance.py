"""Важность события: breaking / notable / routine.

Скоринг до этого мерил плотность ключей и возраст — «крупный анонс» и «рутинный
пост» с одинаковыми словами получали одинаковый вес. Этот чек сводит уже
вычисленные сигналы в уровень значимости:

- first-party: источник с весом >=1.2 (Google/Yandex/Bing/официальные блоги) —
  анонс из первых рук, а не пересказ;
- горячий триггер: viral-скор от словаря тяжёлых тем (санкции/апдейты/волатильность);
- подтверждение: verify_update сверяет апдейт с Google Search Status (Sprint 1B);
- распространение: momentum — сколько независимых источников пишут о теме.

breaking = подтверждённый апдейт ИЛИ (первоисточник × горячий триггер) ИЛИ
(всплеск × горячий триггер). notable = один сильный сигнал. Иначе routine.

Sprint 3 (quality-journalism). Fails open: любая ошибка → routine без бонуса.
"""

import logging

logger = logging.getLogger(__name__)

# Пороги на существующих шкалах (viral 0-100, momentum level из checks/momentum.py)
_HOT_VIRAL = 60        # тяжёлая тема: санкции/апдейт/волатильность + буст сущности
_WARM_VIRAL = 40       # заметная тема
_FIRST_PARTY_W = 1.2   # source_weight первоисточников (source_weight.py)
_SPREAD_LEVELS = {"viral", "growing"}  # >=4 источников за 1ч/6ч

SIGNIFICANCE_BONUS = {"breaking": 12, "notable": 5, "routine": 0}

# Периодика — рекапы/дайджесты/подборки. Формат по определению не breaking, даже
# от первоисточника с горячим триггером: это освещение событий, не само событие.
import re as _re
_PERIODIC_RE = _re.compile(
    r"recap|roundup|digest|дайджест|weekly|monthly|newsletter|итоги (недели|месяца|дня)",
    _re.IGNORECASE)


def event_significance(news: dict, viral: dict, momentum: dict, source_weight: float) -> dict:
    """Возвращает {"level", "score_bonus", "signals"} — уровень значимости события."""
    signals = []
    try:
        viral_score = (viral or {}).get("score", 0) or 0
        m_level = (momentum or {}).get("level", "") or ""

        first_party = source_weight >= _FIRST_PARTY_W
        hot = viral_score >= _HOT_VIRAL
        warm = viral_score >= _WARM_VIRAL
        spread = m_level in _SPREAD_LEVELS

        if first_party:
            signals.append("first_party")
        if hot:
            signals.append("hot_trigger")
        if spread:
            signals.append("momentum_spread")

        confirmed = False
        try:
            from checks.update_verification import verify_update
            # Только ЗАГОЛОВОК: рекапы («Daily Recap», «Greenlane Index») упоминают
            # апдейт в теле — это освещение, не breaking. Сам апдейт заявлен в тайтле.
            v = verify_update(news.get("title", ""), "")
            confirmed = bool(v.get("confirmed"))
            if confirmed:
                signals.append("confirmed_update")
        except Exception as e:
            logger.debug("significance: verify_update skipped: %s", e)

        if confirmed or (first_party and hot) or (spread and hot):
            level = "breaking"
        elif hot or spread or (first_party and warm):
            level = "notable"
        else:
            level = "routine"

        # Периодика не бывает breaking — максимум notable
        if level == "breaking" and _PERIODIC_RE.search(news.get("title", "") or ""):
            level = "notable"
            signals.append("periodic_capped")
    except Exception as e:
        logger.debug("significance failed open: %s", e)
        return {"level": "routine", "score_bonus": 0, "signals": []}

    return {"level": level, "score_bonus": SIGNIFICANCE_BONUS[level], "signals": signals}
