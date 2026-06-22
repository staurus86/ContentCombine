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

# Стоп-слова для игровых новостей (рус + англ)
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

# Фоновый корпус: типичные фразы из игровых новостей (рус + англ, сбалансировано).
BACKGROUND_CORPUS = [
    # Русские — общие игровые новости
    "новая игра вышла на pc и консоли с большим обновлением",
    "разработчики выпустили патч обновление для игры",
    "трейлер новой игры показали на презентации",
    "студия анонсировала продолжение популярной серии",
    "релиз игры перенесли на следующий год",
    "компания представила новый геймплей на выставке",
    "обзор игры показал высокие оценки критиков",
    "игроки обнаружили баг в последнем обновлении",
    "дополнение к игре выйдет в следующем месяце",
    "бесплатное обновление добавляет новый контент и режимы",
    "киберспортивный турнир собрал рекордный призовой фонд",
    "издатель закрыл студию разработчиков после провала",
    "эксклюзив консоли выходит на другие платформы",
    "ремейк классической игры получил дату релиза",
    "утечка раскрыла подробности неанонсированного проекта",
    "сервис подписки пополнился новыми играми",
    "ранний доступ стартовал в steam и epic games store",
    "разработчики рассказали о планах на будущее игры",
    "сиквел получил первый геймплейный трейлер",
    "мобильная версия игры выходит на ios и android",
    # Английские — общие игровые новости
    "new game announced with release date trailer",
    "developer studio released major update patch",
    "upcoming game revealed gameplay trailer first look",
    "publisher announced new title coming to platforms",
    "early access launch available on steam epic store",
    "game review scores metacritic opencritic rating",
    "esports tournament championship prize pool winner",
    "dlc expansion season pass new content update",
    "free to play battle royale shooter open world",
    "console exclusive port remaster remake collection",
    "studio shut down after layoffs restructuring",
    "leak reveals unannounced game sequel project",
    "subscription service adds new games this month",
    "remake remaster classic game gets release date",
    "mobile version launches on ios android devices",
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
            # Filter vocabulary by ngram_range (check word count in each term)
            n_min, n_max = ngram_range
            filtered_vocab = {
                term: idx for term, idx in vocab.items()
                if n_min <= len(term.split()) <= n_max
            }
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
    Background corpus provides IDF dampening for common gaming phrases.
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
