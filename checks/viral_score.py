import config

VIRAL_TRIGGERS = {
    "aisearch_high": {
        "label": "AI-поиск · крит.",
        "weight": 30,
        "keywords": ["ai citation", "ai mode", "ai overviews", "ai-overzichten", "aiによる概要", "answer engine", "aperçus ia", "chatgpt", "chatgpt search", "gemini", "generative engine optimization", "generative search", "ki-übersichten", "panoramiche ai", "perplexity", "przeglądy ai", "resúmenes de ia", "share of voice в вебмастере", "visões gerais com ia", "yandexgpt", "yapay zeka genel bakışları", "алиса ai", "видимость в алисе", "генеративный поиск", "ии-поиск", "нейро", "нейроответы", "нейроответы яндекса", "поиск с алисой", "цитирование в ии", "цитируемость в ии"],
    },
    "aisearch_med": {
        "label": "AI-поиск · средн.",
        "weight": 15,
        "keywords": ["ai検索", "búsqueda con ia", "copilot", "ki-suche", "llms.txt", "pesquisa com ia", "recherche ia", "ricerca con ia", "нейросеть", "answer engine optimization", "geo optimization", "geo-оптимизация", "оптимизация под нейросети", "оптимизация под ии", "llmo", "llm optimization", "brand mention", "mention your brand", "упоминание бренда", "цитируемость бренда", "видимость бренда в ии"],
    },
    "algoupd_high": {
        "label": "Апдейт алгоритма · крит.",
        "weight": 40,
        "keywords": ["actualización del algoritmo", "actualización principal", "aggiornamento dell'algoritmo", "aggiornamento principale", "aktualizacja algorytmu", "aktualizacja podstawowa", "algorithmus-update", "algoritma güncellemesi", "algoritme-update", "atualização do algoritmo", "atualização principal", "broad core update", "core update", "helpful content system", "helpful content update", "kern-update", "mise à jour de l'algorithme", "mise à jour principale", "product reviews update", "site reputation abuse", "site reputation abuse update", "spam update", "yati", "çekirdek güncellemesi", "алгоритм качества контента", "апдейт", "апдейт алгоритма", "базовый апдейт", "вега", "выкатили апдейт", "завершилась раскатка", "обновление алгоритма", "оригами", "прокатился апдейт", "раскатка алгоритма", "спам-апдейт", "тайфун", "アルゴリズムアップデート", "コアアップデート"],
    },
    "algoupd_low": {
        "label": "Апдейт алгоритма · слаб.",
        "weight": 8,
        "keywords": ["hummingbird", "medic update", "pigeon"],
    },
    "algoupd_med": {
        "label": "Апдейт алгоритма · средн.",
        "weight": 20,
        "keywords": ["algorithm update", "algorithmus-änderung", "bert", "discover core update", "done rolling out", "fully rolled out", "google update", "panda", "penguin", "rankbrain", "rollout", "unconfirmed update", "королёв", "палех", "アルゴリズム変動"],
    },
    "content_low": {
        "label": "Контент · слаб.",
        "weight": 3,
        "keywords": ["авторитетность"],
    },
    "content_med": {
        "label": "Контент · средн.",
        "weight": 8,
        "keywords": ["ai content", "ai-generated content", "ai-тексты", "content pruning", "e-e-a-t", "helpful content", "mass-produced content", "thin content", "topical authority", "некачественный контент", "сгенерированный контент", "экспертность контента", "ai detector", "ai detection", "ai content detection", "детектор ии", "обнаружение ии-контента"],
    },
    "link_high": {
        "label": "Ссылки · крит.",
        "weight": 18,
        "keywords": ["обесценивание ссылок", "ссылочные сетки", "фильтрация ссылок"],
    },
    "link_low": {
        "label": "Ссылки · слаб.",
        "weight": 4,
        "keywords": ["anchor text", "guest post", "link exchange", "link velocity", "nofollow", "крауд-ссылки", "pbn", "private blog network", "сетка сайтов", "сетка дропов", "дроп-домен", "дроп-домены", "сателлиты"],
    },
    "link_med": {
        "label": "Ссылки · средн.",
        "weight": 9,
        "keywords": ["backlink", "backlinks", "disavow", "link building", "toxic links", "обратные ссылки", "покупные ссылки", "ссылочный профиль", "токсичные ссылки"],
    },
    "measure_low": {
        "label": "Метрики · слаб.",
        "weight": 3,
        "keywords": ["click-through rate", "impressions", "organic traffic", "share of voice"],
    },
    "measure_med": {
        "label": "Метрики · средн.",
        "weight": 6,
        "keywords": ["google search console", "search console"],
    },
    "penalty_high": {
        "label": "Санкции · крит.",
        "weight": 45,
        "keywords": ["abstrafung", "acción manual", "action manuelle", "azione manuale", "ação manual", "ceza", "deindeksacja", "deindexed", "deindexiert", "deindexing", "deindicizzato", "demotion", "deranked", "desindexado", "dizinden çıkarma", "désindexé", "expired domain abuse", "filtr", "gedeïndexeerd", "google penalty", "handmatige actie", "kara ręczna", "manual action", "manual penalty", "manuel işlem", "manuelle maßnahme", "parasite seo", "penalización", "penalização", "penalizzazione", "penalty", "pure spam", "pénalité", "scaled content abuse", "spam policies", "spam policy", "антиспам яндекса", "антифрод", "аффилиат-фильтр", "баден-баден", "выпал из индекса", "выпал из поиска", "забанили", "исключён из поиска", "минусинск", "наказание за спам", "накрутка пф", "наложили фильтр", "пессимизация", "попал под фильтр", "ручные меры", "ручные санкции", "санкции", "снят фильтр", "спам-фильтр", "фильтр за накрутку пф", "インデックス削除", "ペナルティ", "手動による対策"],
    },
    "penalty_med": {
        "label": "Санкции · средн.",
        "weight": 22,
        "keywords": ["cloaking", "link spam", "reconsideration request", "unnatural links"],
    },
    "ranking_med": {
        "label": "Ранжирование · средн.",
        "weight": 6,
        "keywords": ["featured snippet", "ranking factor", "коммерческие факторы", "поведенческие факторы", "факторы ранжирования"],
    },
    "technical_high": {
        "label": "Техничка · крит.",
        "weight": 20,
        "keywords": ["indexing bug", "баг индексации", "деиндексация", "проблемы с индексацией", "страницы не индексируются"],
    },
    "technical_low": {
        "label": "Техничка · слаб.",
        "weight": 4,
        "keywords": ["301 редирект", "soft 404"],
    },
    "technical_med": {
        "label": "Техничка · средн.",
        "weight": 10,
        "keywords": ["canonical", "core web vitals", "crawl budget", "googlebot", "indexing", "javascript seo", "mobile-first indexing", "noindex", "page experience", "rendering", "robots.txt", "дубли", "индексация", "краулинг", "краулинговый бюджет", "микроразметка", "переиндексация", "переобход", "структурированные данные"],
    },
    "urgency_high": {
        "label": "Срочное · крит.",
        "weight": 20,
        "keywords": ["api leak", "breaking", "confirmed", "content warehouse", "google announces", "google api leak", "google bestätigt", "google bevestigt", "google conferma", "google confirma", "google confirma pt", "google confirme", "google confirms", "google warns", "google подтвердил", "leak", "shutting down", "выкатили", "закрывают", "отключают", "официально", "подтвердил google", "прекращает поддержку", "сбой в поиске", "яндекс подтвердил"],
    },
    "urgency_med": {
        "label": "Срочное · средн.",
        "weight": 10,
        "keywords": ["deadline", "deprecated", "déploiement", "google says", "now live", "rolling out", "sunset", "urgent", "warning", "впервые", "дедлайн", "запустили", "массово", "срочно", "ロールアウト"],
    },
    "volatility_high": {
        "label": "Волатильность · крит.",
        "weight": 38,
        "keywords": ["calo di posizioni", "caída de posiciones", "chute de positions", "lost rankings", "lost traffic", "perda de tráfego", "perdita di traffico", "perte de trafic", "pérdida de tráfico", "queda de posições", "ranking drop", "ranking volatility", "rankingdaling", "rankingverlust", "recovery", "serp shake-up", "spadek pozycji", "spadek ruchu", "sıralama düşüşü", "traffic crash", "traffic drop", "traffic loss", "traffic recovery", "traffic-einbruch", "trafik kaybı", "verkeersdaling", "visibility drop", "volatility", "восстановление после апдейта", "восстановление трафика", "вылет из топ-10", "вылет из топа", "глюк выдачи", "обвал позиций", "обвал трафика", "обрушение трафика", "падение позиций", "падение трафика", "переранжирование", "перетряска выдачи", "потеря трафика", "просадка позиций", "просадка трафика", "сильная волатильность", "схлопнулся трафик", "турбулентность выдачи", "шторм выдачи", "штормит выдача", "トラフィック減少", "順位下落"],
    },
    "volatility_med": {
        "label": "Волатильность · средн.",
        "weight": 18,
        "keywords": ["erholung", "odzyskiwanie pozycji", "plummeted", "ranking fluctuations", "ranking surge", "recuperación", "recuperação", "recupero", "récupération", "sichtbarkeitsverlust", "tanked", "взлёт трафика", "восстановление позиций"],
    },
    "agentic_high": {
        "label": "AI-агенты · крит.",
        "weight": 24,
        "keywords": ["ai agent", "ai agents", "agentic", "agentic ai", "agentic search", "agentic browsing", "autonomous agent", "openai operator", "computer use", "browser agent", "ии-агент", "ии-агенты", "ai-агент", "ai-агенты", "агентный поиск", "автономный агент"],
    },
    "agentic_med": {
        "label": "AI-агенты · средн.",
        "weight": 12,
        "keywords": ["ai assistant", "ии-ассистент", "автономные агенты", "agent workflow", "agentic workflow"],
    },
    "crawler_high": {
        "label": "AI-краулеры · крит.",
        "weight": 18,
        "keywords": ["gptbot", "google-extended", "ai crawler", "ai-краулер", "claudebot", "perplexitybot", "oai-searchbot", "ccbot", "bytespider", "блокировка ии-ботов", "блокировка ai-ботов", "доступ ии-краулеров"],
    },
    "crawler_med": {
        "label": "AI-краулеры · средн.",
        "weight": 10,
        "keywords": ["applebot-extended", "amazonbot", "anthropic-ai", "ии-бот", "ai-бот", "краулеры ии"],
    },
    "regulation_high": {
        "label": "Регуляции · крит.",
        "weight": 26,
        "keywords": ["antitrust", "monopoly", "department of justice", "digital markets act", "antitrust ruling", "antitrust remedies", "google monopoly", "антимонопольное", "антимонопольный иск", "иск минюста", "регулятор", "разделение google"],
    },
    "regulation_med": {
        "label": "Регуляции · средн.",
        "weight": 13,
        "keywords": ["gdpr", "ftc", "eu commission", "data act", "ai act", "регулирование ии", "закон об ии", "152-фз"],
    },
    "zeroclick_high": {
        "label": "Zero-click · крит.",
        "weight": 20,
        "keywords": ["zero-click", "zero-click search", "zero click", "no-click search", "ноль кликов", "нулевой клик", "zero-click результаты"],
    },
    "zeroclick_med": {
        "label": "Zero-click · средн.",
        "weight": 10,
        "keywords": ["снижение ctr из-за ai", "падение переходов из-за ai overviews", "меньше кликов из выдачи"],
    },
    "modelrelease_high": {
        "label": "Релиз модели · крит.",
        "weight": 16,
        "keywords": ["gpt-5", "gpt-6", "claude opus 4", "claude 4", "gemini 3", "gemini 2.0", "llama 4", "релиз модели", "анонс модели", "выпустили модель", "представили модель", "new ai model"],
    },
    "modelrelease_med": {
        "label": "Релиз модели · средн.",
        "weight": 8,
        "keywords": ["model release", "обновление модели", "новая версия модели", "deepseek", "qwen", "mistral", "grok"],
    },
    "protocol_med": {
        "label": "Протоколы (MCP) · средн.",
        "weight": 10,
        "keywords": ["model context protocol", "mcp server", "mcp-сервер", "протокол mcp", "поддержка mcp", "agent2agent", "agentic protocol"],
    },
    "protocol_low": {
        "label": "Протоколы (MCP) · слаб.",
        "weight": 5,
        "keywords": ["function calling", "tool use standard", "open agent protocol"],
    },
}

from nlp.entities import get_entity_boost, TIER_BOOST

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
    _DEDUP_PREFIXES = {"aisearch", "algoupd", "content", "link", "measure", "penalty", "ranking", "technical", "urgency", "volatility", "agentic", "crawler", "regulation", "zeroclick", "modelrelease", "protocol"}
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
        triggered.append({"id": "leak_big_title", "label": "Утечка + крупная сущность", "weight": 45})

    # Scandal/controversy + big entity combo
    has_scandal = any(kw in text for kw in [
        "скандал", "controversy", "backlash", "outrage", "бойкот", "boycott",
    ])
    if has_scandal and has_big_title:
        score += 25
        triggered.append({"id": "scandal_big_title", "label": "Скандал + крупная сущность", "weight": 25})

    score = min(100, score)

    if score >= config.VIRAL_HIGH_THRESHOLD:
        level = "high"
    elif score >= config.VIRAL_MEDIUM_THRESHOLD:
        level = "medium"
    elif score >= config.VIRAL_LOW_THRESHOLD:
        level = "low"
    else:
        level = "none"

    # Time-decay убран: за возраст отвечает единственный механизм — checks/freshness.py.
    # Раньше возраст штрафовался дважды (freshness-чек + decay здесь), и старая,
    # но важная новость наказывалась квадратично.

    return {"score": score, "level": level, "triggers": triggered, "pass": score >= config.VIRAL_LOW_THRESHOLD}
