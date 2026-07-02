"""Гибридное извлечение ключевых фраз: словарь сущностей + TF-IDF.

Оптимизировано: vectorizer кешируется в памяти и на диске (JSON).
При старте загружается vocabulary из storage/tfidf_vocab_cache.json,
что позволяет использовать transform вместо fit_transform.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from sklearn.feature_extraction.text import TfidfVectorizer
from nlp.game_entities import find_entities, TIER_BOOST

logger = logging.getLogger(__name__)

# --- Constants ---

# Стоп-слова для новостей ниши SEO/AI/маркетинг (рус + англ)
STOP_WORDS = {
    # Русские — местоимения, предлоги, союзы, частицы, вспомогательные
    "это", "как", "что", "для", "при", "все", "они", "его", "она", "мне",
    "так", "или", "уже", "без", "тоже", "может", "будет", "если", "еще",
    "них", "нет", "есть", "был", "быть", "были", "было", "свой", "свои",
    "том", "тот", "этот", "эти", "где", "когда", "чем", "кто", "под",
    "также", "после", "перед", "между", "через", "более", "менее", "очень",
    "только", "просто", "именно", "вот", "лишь", "ведь", "даже", "ещё",
    "которые", "который", "которая", "которое", "которых", "которому",
    "чтобы", "потому", "поэтому", "однако", "хотя", "впрочем", "причём",
    "тем", "нас", "вас", "нам", "вам", "ним", "ней", "ему", "ими",
    "себя", "себе", "собой", "свою", "свое", "своё", "своих", "своим",
    "моя", "мой", "моё", "мои", "наш", "наша", "наше", "наши",
    "какой", "какая", "какие", "какое", "такой", "такая", "такие", "такое",
    "другой", "другая", "другие", "другое", "каждый", "каждая", "каждое",
    "сам", "сама", "само", "сами", "весь", "вся", "всё", "всех",
    "над", "про", "ото", "обо", "надо", "пока", "либо", "иначе",
    "раз", "ещё", "два", "три", "уже", "чуть", "куда", "туда", "сюда",
    "тогда", "теперь", "потом", "затем", "снова", "опять", "здесь", "там",
    "ничего", "никто", "ничто", "никогда", "нигде", "некоторые",
    # Русские — глагольные формы общего употребления
    "стал", "стала", "стало", "стали", "стать",
    "мог", "могла", "могли", "могут", "можно", "нельзя",
    "хочет", "хотят", "хотел", "хотела",
    "должен", "должна", "должно", "должны",
    "говорит", "говорят", "сказал", "сказала", "заявил", "заявила",
    "решил", "решила", "решили", "получил", "получила", "получили",
    "сделал", "сделала", "сделали", "делает", "делают",
    "дал", "дала", "дали", "даёт", "дают", "давать",
    "знает", "знают", "знал", "знала",
    "видно", "видел", "видела", "хорошо", "плохо",
    "надо", "нужно", "нужна", "нужны", "нужен",
    # Английские
    "the", "and", "for", "that", "with", "this", "from", "has", "are",
    "was", "will", "but", "not", "you", "all", "can", "had", "her",
    "one", "our", "out", "its", "have", "been", "who", "more", "new",
    "also", "about", "into", "than", "just", "over", "some", "after",
    "before", "between", "through", "most", "only", "very", "when",
    "where", "which", "while", "being", "would", "could", "should",
    "their", "there", "these", "those", "then", "them", "they", "what",
    "each", "other", "much", "such", "here", "does", "did", "may",
    "like", "well", "back", "even", "still", "many", "made", "said",
    "any", "how", "now", "way", "get", "got", "going", "come",
}

# Фоновый корпус: типичные фразы SEO/AI-поиск-новостей (рус + англ, сбалансировано).
# TF-IDF гасит эти обороты как фон, чтобы ключевыми словами становилась
# специфика статьи, а не жанровые штампы ниши.
BACKGROUND_CORPUS = [
    # Русские — типовые SEO-новости
    "google выпустил обновление основного алгоритма поиска",
    "яндекс объявил об изменениях в ранжировании сайтов",
    "вебмастеры отмечают волатильность выдачи после апдейта",
    "как оптимизировать сайт для поисковых систем руководство",
    "эксперты назвали главные факторы ранжирования в поиске",
    "search console получил новые отчёты для вебмастеров",
    "нейросети меняют подход к поисковой оптимизации",
    "компания представила новый инструмент для seo-специалистов",
    "кейс продвижения сайта в конкурентной нише",
    "исследование показало снижение кликабельности из-за ии-ответов",
    "советы по улучшению видимости сайта в поиске",
    "разбор изменений в поисковой выдаче за месяц",
    "специалисты обсуждают влияние ии на поисковый трафик",
    "апдейт завершил раскатку сайты видят изменения позиций",
    "чек-лист технического аудита сайта для новичков",
    "стратегия контент-маркетинга для роста органического трафика",
    "обновление правил для рекламы и монетизации сайтов",
    "интервью с экспертом о будущем поисковой оптимизации",
    "сравнение инструментов для анализа ключевых слов",
    "дайджест новостей интернет-маркетинга за неделю",
    # Английские — типовые SEO-новости
    "google announces core algorithm update rolling out",
    "search console adds new report for webmasters",
    "study shows ai overviews impact on organic traffic",
    "how to optimize your website for search engines guide",
    "seo experts share ranking factors analysis",
    "new tool helps track keyword rankings and visibility",
    "case study organic traffic growth after content update",
    "tips to improve click through rate in search results",
    "ai search changes how users find websites",
    "conference recap latest trends in digital marketing",
    "chatgpt and perplexity cite websites in answers",
    "site recovered traffic after core update rollout",
    "structured data schema markup best practices guide",
    "link building strategies that still work this year",
    "algorithm update volatility reported by rank tracking tools",
]

_STOP_WORDS_LIST = list(STOP_WORDS)

# Pre-compiled regex for text cleaning
_RE_HTML = re.compile(r"<[^>]+>")
_RE_NONWORD = re.compile(r"[^\w\s\-]")
_RE_SPACES = re.compile(r"\s+")
# Regex for Cyrillic token pattern (allows words from both alphabets)
_TOKEN_PATTERN = r"(?u)\b[a-zA-Zа-яА-ЯёЁ0-9][a-zA-Zа-яА-ЯёЁ0-9\-]+\b"

# --- Persistent disk cache for TF-IDF vocabulary ---
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "storage")
_CACHE_PATH = os.path.join(_CACHE_DIR, "tfidf_vocab_cache.json")

# In-memory cached vectorizers keyed by ngram_range
_cached_vectorizers: dict[tuple, TfidfVectorizer] = {}


# --- Disk cache helpers ---

def _load_vocab_cache() -> dict | None:
    """Load vocabulary cache from disk. Returns dict or None."""
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "vocabulary" in data and isinstance(data["vocabulary"], dict):
            logger.info("TF-IDF vocab cache loaded from disk (%d terms, updated %s)",
                        len(data["vocabulary"]), data.get("updated_at", "?"))
            return data
        return None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_vocab_cache(vocabulary: dict, doc_count: int) -> None:
    """Save vocabulary to disk cache."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_CACHE_PATH)), exist_ok=True)
        data = {
            "vocabulary": vocabulary,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "doc_count": doc_count,
        }
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        logger.info("TF-IDF vocab cache saved to disk (%d terms)", len(vocabulary))
    except OSError as e:
        logger.warning("Failed to save TF-IDF vocab cache: %s", e)


def _get_vectorizer(ngram_range: tuple, force_refit: bool = False) -> TfidfVectorizer | None:
    """Get a fitted vectorizer, using disk cache if available.

    Returns a vectorizer fitted on the background corpus, or None if
    force_refit is requested (caller should fit manually).
    """
    # Check in-memory cache first
    if not force_refit and ngram_range in _cached_vectorizers:
        return _cached_vectorizers[ngram_range]

    # Try loading from disk cache
    if not force_refit:
        cache_data = _load_vocab_cache()
        if cache_data is not None:
            vocab = cache_data["vocabulary"]
            # Filter vocabulary by ngram_range (check word count in each term).
            # Reindex to a contiguous 0..n-1 range: cache stores bi- and trigrams
            # in one shared index space, so a plain filter leaves gaps (trigrams
            # start at B, not 0) and sklearn rejects it with «doesn't contain
            # index 0» after a restart — silently killing trigram/bigram extraction.
            n_min, n_max = ngram_range
            filtered_terms = sorted(
                (term for term in vocab if n_min <= len(term.split()) <= n_max),
                key=lambda t: vocab[t],
            )
            filtered_vocab = {term: i for i, term in enumerate(filtered_terms)}
            if filtered_vocab:
                vectorizer = TfidfVectorizer(
                    ngram_range=ngram_range,
                    max_features=200,
                    stop_words=_STOP_WORDS_LIST,
                    min_df=1,
                    token_pattern=_TOKEN_PATTERN,
                    vocabulary=filtered_vocab,
                )
                # fit on background corpus to compute IDF weights with the fixed vocabulary
                vectorizer.fit(BACKGROUND_CORPUS)
                _cached_vectorizers[ngram_range] = vectorizer
                return vectorizer

    return None


def _save_after_fit(vectorizer: TfidfVectorizer) -> None:
    """Merge newly fitted vocabulary into disk cache and save."""
    existing = _load_vocab_cache()
    if existing and "vocabulary" in existing:
        vocab = existing["vocabulary"]
    else:
        vocab = {}

    next_idx = max(vocab.values(), default=-1) + 1
    for term in vectorizer.get_feature_names_out():
        if term not in vocab:
            vocab[term] = next_idx
            next_idx += 1

    _save_vocab_cache(vocab, len(BACKGROUND_CORPUS))


def rebuild_vocab_cache() -> None:
    """Force re-fit on background corpus and save vocabulary to disk.

    Call this when the background corpus or stop words change.
    """
    global _cached_vectorizers
    _cached_vectorizers.clear()

    all_vocab: dict[str, int] = {}
    idx = 0

    for ngram_range in [(2, 2), (3, 3)]:
        vectorizer = TfidfVectorizer(
            ngram_range=ngram_range,
            max_features=200,
            stop_words=_STOP_WORDS_LIST,
            min_df=1,
            token_pattern=_TOKEN_PATTERN,
        )
        vectorizer.fit_transform(BACKGROUND_CORPUS)
        for term in vectorizer.get_feature_names_out():
            if term not in all_vocab:
                all_vocab[term] = idx
                idx += 1
        _cached_vectorizers[ngram_range] = vectorizer

    _save_vocab_cache(all_vocab, len(BACKGROUND_CORPUS))
    logger.info("TF-IDF vocab cache rebuilt: %d terms across all ngram ranges", len(all_vocab))


# --- Core functions ---

def clean_text(text: str) -> str:
    """Очищает текст от HTML, лишних символов."""
    text = _RE_HTML.sub(" ", text)
    text = _RE_NONWORD.sub(" ", text)
    text = _RE_SPACES.sub(" ", text)
    return text.strip().lower()


def _tfidf_with_background(text: str, ngram_range: tuple, top_n: int,
                            force_refit: bool = False) -> list[list]:
    """TF-IDF с фоновым корпусом.

    Uses cached vectorizer (from disk or memory) when available.
    Falls back to full fit_transform on corpus+text for new terms.
    Background corpus provides IDF dampening for common SEO/marketing phrases.
    """
    try:
        cached = _get_vectorizer(ngram_range, force_refit=force_refit)
        if cached is not None:
            # Use cached vocabulary — transform only (much faster)
            corpus = [text] + BACKGROUND_CORPUS
            tfidf_matrix = cached.transform(corpus)
            feature_names = cached.get_feature_names_out()
            scores = tfidf_matrix.toarray()[0]
        else:
            # No cache — full fit_transform, then save cache
            corpus = [text] + BACKGROUND_CORPUS
            vectorizer = TfidfVectorizer(
                ngram_range=ngram_range,
                max_features=200,
                stop_words=_STOP_WORDS_LIST,
                min_df=1,
                token_pattern=_TOKEN_PATTERN,
            )
            tfidf_matrix = vectorizer.fit_transform(corpus)
            feature_names = vectorizer.get_feature_names_out()
            scores = tfidf_matrix.toarray()[0]

            # Cache in memory
            _cached_vectorizers[ngram_range] = vectorizer

            # Save vocabulary to disk (merge with existing if any)
            _save_after_fit(vectorizer)

        ranked = sorted(
            zip(feature_names, scores),
            key=lambda x: x[1],
            reverse=True,
        )
        return [[phrase, round(float(score), 4)] for phrase, score in ranked if score > 0][:top_n]
    except ValueError:
        return []


def extract_keywords(text: str, top_n: int = 10) -> dict:
    """Извлекает ключевые фразы гибридным методом."""
    cleaned = clean_text(text)
    if len(cleaned.split()) < 3:
        return {"bigrams": [], "trigrams": [], "entities": []}

    # 1. Извлекаем известные сущности
    entities = find_entities(text)

    # 2. TF-IDF с фоновым корпусом
    tfidf_bigrams = _tfidf_with_background(cleaned, (2, 2), top_n * 2)
    tfidf_trigrams = _tfidf_with_background(cleaned, (3, 3), top_n)

    # 3. Бустим биграммы/триграммы, совпадающие с сущностями
    entity_names_lower = set()
    entity_tier_map = {}
    for ent in entities:
        name = ent["name"].lower()
        entity_names_lower.add(name)
        entity_tier_map[name] = ent["tier"]

    def boost_ngrams(ngrams: list[list]) -> list[list]:
        boosted = []
        seen_entities = set()
        for phrase, score in ngrams:
            matched_entity = None
            for ename in entity_names_lower:
                if ename in phrase or phrase in ename:
                    matched_entity = ename
                    break

            if matched_entity:
                tier = entity_tier_map[matched_entity]
                multiplier = {"S": 2.0, "A": 1.5, "B": 1.2, "C": 1.1}.get(tier, 1.0)
                boosted.append([phrase, round(score * multiplier, 4)])
                seen_entities.add(matched_entity)
            else:
                boosted.append([phrase, score])

        for ent in entities:
            name = ent["name"].lower()
            if name not in seen_entities and " " in name:
                tier_score = {"S": 1.0, "A": 0.8, "B": 0.6, "C": 0.4}.get(ent["tier"], 0.3)
                boosted.append([name, tier_score])
                seen_entities.add(name)

        boosted.sort(key=lambda x: x[1], reverse=True)
        return boosted

    bigrams = boost_ngrams(tfidf_bigrams)[:top_n]
    trigrams = boost_ngrams(tfidf_trigrams)[:top_n]

    return {
        "bigrams": bigrams,
        "trigrams": trigrams,
        "entities": entities,
    }
