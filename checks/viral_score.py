from datetime import datetime, timezone
from typing import Optional

import config

VIRAL_TRIGGERS = {
    "aisearch_high": {
        "label": "aisearch/high",
        "weight": 30,
        "keywords": ["ai citation", "ai crawler", "ai mode", "ai overviews", "ai-overzichten", "aiによる概要", "answer engine", "aperçus ia", "chatgpt", "chatgpt search", "gemini", "generative engine optimization", "generative search", "google-extended", "gptbot", "ki-übersichten", "panoramiche ai", "perplexity", "przeglądy ai", "resúmenes de ia", "share of voice в вебмастере", "visões gerais com ia", "yandexgpt", "yapay zeka genel bakışları", "алиса ai", "видимость в алисе", "генеративный поиск", "ии-поиск", "нейро", "нейроответы", "нейроответы яндекса", "поиск с алисой", "цитирование в ии", "цитируемость в ии"],
    },
    "aisearch_med": {
        "label": "aisearch/med",
        "weight": 15,
        "keywords": ["ai-краулер", "ai検索", "búsqueda con ia", "claudebot", "copilot", "ki-suche", "llms.txt", "perplexitybot", "pesquisa com ia", "recherche ia", "ricerca con ia", "нейросеть"],
    },
    "algoupd_high": {
        "label": "algoupd/high",
        "weight": 40,
        "keywords": ["actualización del algoritmo", "actualización principal", "aggiornamento dell'algoritmo", "aggiornamento principale", "aktualizacja algorytmu", "aktualizacja podstawowa", "algorithmus-update", "algoritma güncellemesi", "algoritme-update", "atualização do algoritmo", "atualização principal", "august 2025 spam update", "broad core update", "core update", "december 2025 core update", "helpful content system", "helpful content update", "june 2025 core update", "kern-update", "march 2025 core update", "march 2026 core update", "march 2026 spam update", "may 2026 core update", "mise à jour de l'algorithme", "mise à jour principale", "product reviews update", "site reputation abuse", "site reputation abuse update", "spam update", "yati", "çekirdek güncellemesi", "алгоритм качества контента", "апдейт", "апдейт алгоритма", "базовый апдейт", "вега", "выкатили апдейт", "завершилась раскатка", "обновление алгоритма", "оригами", "прокатился апдейт", "раскатка алгоритма", "спам-апдейт", "тайфун", "アルゴリズムアップデート", "コアアップデート"],
    },
    "algoupd_low": {
        "label": "algoupd/low",
        "weight": 8,
        "keywords": ["hummingbird", "medic update", "pigeon"],
    },
    "algoupd_med": {
        "label": "algoupd/med",
        "weight": 20,
        "keywords": ["algorithm update", "algorithmus-änderung", "bert", "discover core update", "done rolling out", "fully rolled out", "google update", "panda", "penguin", "rankbrain", "rollout", "unconfirmed update", "королёв", "палех", "アルゴリズム変動"],
    },
    "content_low": {
        "label": "content/low",
        "weight": 3,
        "keywords": ["авторитетность"],
    },
    "content_med": {
        "label": "content/med",
        "weight": 8,
        "keywords": ["ai content", "ai-generated content", "ai-тексты", "content pruning", "e-e-a-t", "helpful content", "mass-produced content", "thin content", "topical authority", "некачественный контент", "сгенерированный контент", "экспертность контента"],
    },
    "link_high": {
        "label": "link/high",
        "weight": 18,
        "keywords": ["обесценивание ссылок", "ссылочные сетки", "фильтрация ссылок"],
    },
    "link_low": {
        "label": "link/low",
        "weight": 4,
        "keywords": ["anchor text", "guest post", "link exchange", "link velocity", "nofollow", "крауд-ссылки"],
    },
    "link_med": {
        "label": "link/med",
        "weight": 9,
        "keywords": ["backlink", "backlinks", "disavow", "link building", "toxic links", "обратные ссылки", "покупные ссылки", "ссылочный профиль", "токсичные ссылки"],
    },
    "measure_low": {
        "label": "measure/low",
        "weight": 3,
        "keywords": ["click-through rate", "impressions", "organic traffic", "share of voice"],
    },
    "measure_med": {
        "label": "measure/med",
        "weight": 6,
        "keywords": ["google search console", "search console"],
    },
    "penalty_high": {
        "label": "penalty/high",
        "weight": 45,
        "keywords": ["abstrafung", "acción manual", "action manuelle", "azione manuale", "ação manual", "ceza", "deindeksacja", "deindexed", "deindexiert", "deindexing", "deindicizzato", "demotion", "deranked", "desindexado", "dizinden çıkarma", "désindexé", "expired domain abuse", "filtr", "gedeïndexeerd", "google penalty", "handmatige actie", "kara ręczna", "manual action", "manual penalty", "manuel işlem", "manuelle maßnahme", "parasite seo", "penalización", "penalização", "penalizzazione", "penalty", "pure spam", "pénalité", "scaled content abuse", "spam policies", "spam policy", "антиспам яндекса", "антифрод", "аффилиат-фильтр", "баден-баден", "выпал из индекса", "выпал из поиска", "забанили", "исключён из поиска", "минусинск", "наказание за спам", "накрутка пф", "наложили фильтр", "пессимизация", "попал под фильтр", "ручные меры", "ручные санкции", "санкции", "снят фильтр", "спам-фильтр", "фильтр за накрутку пф", "インデックス削除", "ペナルティ", "手動による対策"],
    },
    "penalty_med": {
        "label": "penalty/med",
        "weight": 22,
        "keywords": ["cloaking", "disavow", "link spam", "reconsideration request", "unnatural links"],
    },
    "ranking_med": {
        "label": "ranking/med",
        "weight": 6,
        "keywords": ["featured snippet", "ranking factor", "коммерческие факторы", "поведенческие факторы", "факторы ранжирования"],
    },
    "technical_high": {
        "label": "technical/high",
        "weight": 20,
        "keywords": ["indexing bug", "баг индексации", "деиндексация", "проблемы с индексацией", "страницы не индексируются"],
    },
    "technical_low": {
        "label": "technical/low",
        "weight": 4,
        "keywords": ["301 редирект", "soft 404"],
    },
    "technical_med": {
        "label": "technical/med",
        "weight": 10,
        "keywords": ["canonical", "core web vitals", "crawl budget", "googlebot", "indexing", "javascript seo", "mobile-first indexing", "noindex", "page experience", "rendering", "robots.txt", "дубли", "индексация", "краулинг", "краулинговый бюджет", "микроразметка", "переиндексация", "переобход", "структурированные данные"],
    },
    "urgency_high": {
        "label": "urgency/high",
        "weight": 20,
        "keywords": ["antitrust", "api leak", "breaking", "confirmed", "content warehouse", "google announces", "google api leak", "google bestätigt", "google bevestigt", "google conferma", "google confirma", "google confirma pt", "google confirme", "google confirms", "google doğruladı", "google potwierdza", "google warns", "google подтвердил", "leak", "monopoly", "shutting down", "выкатили", "закрывают", "отключают", "официально", "подтвердил google", "прекращает поддержку", "сбой в поиске", "яндекс подтвердил"],
    },
    "urgency_med": {
        "label": "urgency/med",
        "weight": 10,
        "keywords": ["deadline", "deprecated", "déploiement", "google says", "now live", "rolling out", "sunset", "urgent", "warning", "впервые", "дедлайн", "запустили", "массово", "срочно", "ロールアウト"],
    },
    "volatility_high": {
        "label": "volatility/high",
        "weight": 38,
        "keywords": ["calo di posizioni", "caída de posiciones", "chute de positions", "lost rankings", "lost traffic", "perda de tráfego", "perdita di traffico", "perte de trafic", "pérdida de tráfico", "queda de posições", "ranking drop", "ranking volatility", "rankingdaling", "rankingverlust", "recovery", "serp shake-up", "spadek pozycji", "spadek ruchu", "sıralama düşüşü", "traffic crash", "traffic drop", "traffic loss", "traffic recovery", "traffic-einbruch", "trafik kaybı", "verkeersdaling", "visibility drop", "volatility", "zero-click", "zero-click search", "восстановление после апдейта", "восстановление трафика", "вылет из топ-10", "вылет из топа", "глюк выдачи", "обвал позиций", "обвал трафика", "обрушение трафика", "падение позиций", "падение трафика", "переранжирование", "перетряска выдачи", "потеря трафика", "просадка позиций", "просадка трафика", "сильная волатильность", "схлопнулся трафик", "турбулентность выдачи", "шторм выдачи", "штормит выдача", "トラフィック減少", "順位下落"],
    },
    "volatility_med": {
        "label": "volatility/med",
        "weight": 18,
        "keywords": ["erholung", "odzyskiwanie pozycji", "plummeted", "ranking fluctuations", "ranking surge", "recuperación", "recuperação", "recupero", "récupération", "sichtbarkeitsverlust", "tanked", "взлёт трафика", "восстановление позиций"],
    },
}

from nlp.game_entities import get_entity_boost, TIER_BOOST

import json as _json
import logging as _logging

_vlog = _logging.getLogger(__name__)

# ─── Merge default + DB triggers, rebuild lookup ─────────────────────

_KEYWORD_TO_TRIGGER = {}
_SORTED_KEYWORDS = []


def _rebuild_trigger_index():
    """Перестроить индекс ключевых слов из дефолтов + БД."""
    global _KEYWORD_TO_TRIGGER, _SORTED_KEYWORDS

    merged = {}
    # 1. Defaults
    for tid, tdata in VIRAL_TRIGGERS.items():
        merged[tid] = tdata.copy()

    # 2. DB overrides / custom triggers
    try:
        from storage.database import get_connection, _is_postgres
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT trigger_id, label, weight, keywords, is_active FROM viral_triggers_config")
            for row in cur.fetchall():
                if _is_postgres():
                    tid, label, weight, kw_json, active = row
                else:
                    tid, label, weight, kw_json, active = row["trigger_id"], row["label"], row["weight"], row["keywords"], row["is_active"]
                if not active:
                    merged.pop(tid, None)
                    continue
                kws = _json.loads(kw_json) if isinstance(kw_json, str) else (kw_json or [])
                merged[tid] = {"label": label, "weight": weight, "keywords": kws}
        finally:
            cur.close()
    except Exception as e:
        _vlog.debug("Viral triggers DB load skipped: %s", e)

    # Rebuild index
    kw_map = {}
    for tid, tdata in merged.items():
        for kw in tdata.get("keywords", []):
            if kw not in kw_map:
                kw_map[kw] = []
            kw_map[kw].append((tid, tdata["label"], tdata["weight"]))

    _KEYWORD_TO_TRIGGER = kw_map
    _SORTED_KEYWORDS = sorted(kw_map.keys(), key=len, reverse=True)


def reload_viral_triggers():
    """Публичная функция для перезагрузки триггеров (после изменений в БД)."""
    _rebuild_trigger_index()


# Initial build
_rebuild_trigger_index()

GAMING_EVENTS_CALENDAR = []


def get_calendar_boost(dt: Optional[datetime] = None) -> tuple[int, str]:
    if dt is None:
        dt = datetime.now(timezone.utc)
    for month, day_start, day_end, name, boost in GAMING_EVENTS_CALENDAR:
        if dt.month == month and day_start <= dt.day <= day_end:
            return boost, name
    return 0, ""


def viral_score(news: dict, precomputed_entities: list = None) -> dict:
    title = news.get("title", "").lower()
    plain = news.get("plain_text", "") or news.get("description", "") or ""
    text = (title + " " + plain).lower()

    score = 0
    triggered = []

    # Optimized: scan keywords once, collect triggered IDs (avoids N*M loops)
    triggered_ids = set()
    for kw in _SORTED_KEYWORDS:
        if kw in text:
            for tid, label, weight in _KEYWORD_TO_TRIGGER[kw]:
                if tid not in triggered_ids:
                    triggered_ids.add(tid)
                    score += weight
                    triggered.append({"id": tid, "label": label, "weight": weight})

    # Deduplicate: keep only highest-weight trigger per category
    _DEDUP_PREFIXES = {"aisearch", "algoupd", "content", "link", "measure", "penalty", "ranking", "technical", "urgency", "volatility"}
    if len(triggered) > 1:
        best_per_cat = {}  # prefix -> best trigger
        non_cat = []  # triggers without dedup prefix
        for t in triggered:
            prefix = t["id"].split("_")[0] if "_" in t["id"] else ""
            if prefix in _DEDUP_PREFIXES:
                if prefix not in best_per_cat or t["weight"] > best_per_cat[prefix]["weight"]:
                    best_per_cat[prefix] = t
            else:
                non_cat.append(t)
        deduped = list(best_per_cat.values()) + non_cat
        removed_score = sum(t["weight"] for t in triggered) - sum(t["weight"] for t in deduped)
        if removed_score > 0:
            score -= removed_score
            deduped.append({"id": "dedup", "label": f"Dedup (-{removed_score})", "weight": -removed_score})
        triggered = deduped

    # Entity-based boost — reuse precomputed if available
    if precomputed_entities is not None:
        entities = precomputed_entities
        if entities:
            best_tier = entities[0]["tier"]
            entity_boost = TIER_BOOST.get(best_tier, 0)
        else:
            entity_boost = 0
    else:
        entity_boost, entities = get_entity_boost(text)
    has_big_title = entity_boost >= TIER_BOOST["A"]  # A-tier и выше
    if entity_boost > 0:
        score += entity_boost
        best = entities[0] if entities else {}
        triggered.append({
            "id": "entity_boost",
            "label": f"Тег {best.get('tier', '?')}: {best.get('name', '?')}",
            "weight": entity_boost,
        })

    # Big title + leak combo
    has_leak = any(kw in text for kw in ["leak", "leaked", "утечка", "слив", "инсайдер"])
    if has_leak and has_big_title:
        score += 45
        triggered.append({"id": "leak_big_title", "label": "Утечка + крупный тайтл", "weight": 45})

    # Big title + studio closure combo
    has_closure = any(kw in text for kw in ["закрытие студии", "studio closure", "shut down", "студию закрыли"])
    if has_closure and has_big_title:
        score += 30
        triggered.append({"id": "closure_big_title", "label": "Закрытие крупной студии", "weight": 30})

    # Lawsuit + Big company combo
    has_lawsuit = any(kw in text for kw in ["lawsuit", "судебный иск", "court", "sued"])
    has_big_company = any(e.get("type") == "studio" and e.get("tier") in ("S", "A", "B") for e in entities)
    if has_lawsuit and has_big_company:
        score += 20
        triggered.append({"id": "lawsuit_big_company", "label": "Судебный иск + крупная компания", "weight": 20})

    # Scandal/controversy + big title/company combo
    has_scandal = any(kw in text for kw in [
        "скандал", "controversy", "backlash", "outrage", "бойкот", "boycott",
        "скандальный", "сатанист", "шокирующее", "домогательства", "harassment",
    ])
    if has_scandal and has_big_title:
        score += 25
        triggered.append({"id": "scandal_big_title", "label": "Скандал + крупный тайтл/студия", "weight": 25})

    # Calendar boost
    cal_boost, event_name = get_calendar_boost()
    if cal_boost > 0:
        score += cal_boost
        triggered.append({"id": "calendar_event", "label": f"Event: {event_name}", "weight": cal_boost})

    score = min(100, score)

    if score >= config.VIRAL_HIGH_THRESHOLD:
        level = "high"
    elif score >= config.VIRAL_MEDIUM_THRESHOLD:
        level = "medium"
    elif score >= config.VIRAL_LOW_THRESHOLD:
        level = "low"
    else:
        level = "none"

    # Time-decay: fresh news gets full score, older news gets penalized
    age_hours = -1
    pub = news.get("published_at") or news.get("parsed_at") or ""
    if pub:
        try:
            from datetime import datetime, timezone
            if pub.endswith("Z"):
                pub = pub[:-1] + "+00:00"
            pub_dt = datetime.fromisoformat(pub)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
        except Exception:
            pass

    if age_hours >= 0:
        if age_hours < 3:
            decay = 1.0
        elif age_hours < 12:
            decay = 0.9
        elif age_hours < 24:
            decay = 0.75
        elif age_hours < 48:
            decay = 0.5
        else:
            decay = 0.3
        score = round(score * decay)
        if decay < 1.0:
            triggered.append({"id": "time_decay", "label": f"Time decay ({age_hours:.0f}h, x{decay})", "weight": round(score * (decay - 1))})

    return {"score": score, "level": level, "triggers": triggered, "pass": score >= config.VIRAL_LOW_THRESHOLD}
