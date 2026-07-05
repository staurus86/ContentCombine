import hashlib
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from nlp.entities import find_entities


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()


def exact_duplicate(title1: str, title2: str) -> bool:
    return hashlib.md5(normalize(title1).encode()).hexdigest() == \
           hashlib.md5(normalize(title2).encode()).hexdigest()


def entity_overlap(text1: str, text2: str) -> float:
    """Пересечение известных сущностей между двумя текстами (через единую базу)."""
    ents1 = set(e["name"] for e in find_entities(text1))
    ents2 = set(e["name"] for e in find_entities(text2))
    if not ents1 and not ents2:
        return 0
    return len(ents1 & ents2) / max(len(ents1 | ents2), 1)


# --- Clustering tunables (per-niche tuning candidate → niche config later) ---
# entity_overlap alone must NOT create a cluster when entities are ubiquitous
# (e.g. "google"/"chatgpt" in an SEO niche slip every story into one blob), so:
#   1) лексический порог: заголовки должны реально делить слова (TF-IDF) — сигнал
#      сущности лишь *усиливает* уже похожую пару, но не создаёт её сам;
#   2) гипер-частые сущности (в >COMMON_ENTITY_FRAC батча) выкидываются из overlap.
# TF-IDF (реальные слова заголовка) доминирует; вес сущностей низкий, иначе
# вездесущая сущность («claude», «google») склеивает разные истории в блоб
# через транзитивность. Низкий ENTITY_WEIGHT + лексический пол убивают блобы.
TFIDF_FLOOR = 0.24
ENTITY_WEIGHT = 0.18
PAIR_THRESHOLD = 0.40
COMMON_ENTITY_FRAC = 0.07


def tfidf_similarity(titles: list[str], texts: list[str] | None = None,
                     floor: float | None = None, pair_threshold: float | None = None) -> list[tuple]:
    """Комбинированная похожесть: TF-IDF (лексика) + entity overlap (буст).

    Оптимизировано: entity results кешируются, не пересчитываются в O(n²) цикле.

    floor/pair_threshold — переопределяют глобальные пороги. Дедуп («та же новость?»)
    использует дефолты (высокая точность). Сюжеты/тренды («про один сюжет?») передают
    мягче — связь между статьями одного тренда слабее, чем между дублями.
    """
    if len(titles) < 2:
        return []
    floor = TFIDF_FLOOR if floor is None else floor
    pair_threshold = PAIR_THRESHOLD if pair_threshold is None else pair_threshold

    # TF-IDF cosine similarity по заголовкам
    vectorizer = TfidfVectorizer(ngram_range=(1, 2))
    matrix = vectorizer.fit_transform([normalize(t) for t in titles])
    sim = cosine_similarity(matrix)

    compare_texts = texts if texts else titles

    # Предвычисляем entity sets для всех текстов (O(n) вместо O(n²))
    ent_sets = [set(e["name"] for e in find_entities(t)) for t in compare_texts]

    # Выкидываем гипер-частые сущности — они не различают истории
    from collections import Counter as _Counter
    df = _Counter(e for s in ent_sets for e in s)
    common = {e for e, c in df.items() if c > max(8, int(COMMON_ENTITY_FRAC * len(titles)))}
    if common:
        ent_sets = [s - common for s in ent_sets]

    pairs = []
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            tfidf_score = sim[i][j]
            # Лексический порог: без реального совпадения слов пары нет
            if tfidf_score < floor:
                continue

            e1, e2 = ent_sets[i], ent_sets[j]
            ent_score = len(e1 & e2) / max(len(e1 | e2), 1) if (e1 or e2) else 0

            combined = (1 - ENTITY_WEIGHT) * tfidf_score + ENTITY_WEIGHT * ent_score

            if combined > pair_threshold:
                pairs.append((i, j, round(float(combined), 2)))
    return pairs


def recurring_indices(candidate_titles: list[str], prior_titles: list[str]) -> set:
    """Индексы кандидатов, дублирующих УЖЕ опубликованную (prior) историю.

    Для сквозной дедупликации дайджеста между днями: одна и та же история
    (напр. «June 2026 Spam Update») каждый день освещается новыми статьями с
    разными id/url — исключение по id её не ловит, нужна похожесть по смыслу.
    Переиспользует tfidf+entity (tfidf_similarity) + точное совпадение.
    """
    if not prior_titles or not candidate_titles:
        return set()
    n_prior = len(prior_titles)
    out = set()
    # tfidf+entity: пара считается только если пересекает границу prior|candidate
    for i, j, _ in tfidf_similarity(list(prior_titles) + list(candidate_titles)):
        lo, hi = (i, j) if i < j else (j, i)
        if lo < n_prior <= hi:
            out.add(hi - n_prior)
    # короткие заголовки могут не пройти лексический пол — добиваем точным матчем
    for ci, ct in enumerate(candidate_titles):
        if any(exact_duplicate(ct, pt) for pt in prior_titles):
            out.add(ci)
    return out


DUPLICATE_STATUSES = {
    "unique": "unique",
    "popular": "popular",
    "trending": "trending",
    "duplicate": "duplicate",
}


def build_groups(results: list[dict], pairs: list[tuple]) -> list[dict]:
    """Группирует похожие новости."""
    from collections import defaultdict
    graph = defaultdict(set)
    for i, j, score in pairs:
        graph[i].add(j)
        graph[j].add(i)

    visited = set()
    groups = []

    for idx in range(len(results)):
        if idx in visited:
            continue
        group = set()
        stack = [idx]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            group.add(node)
            stack.extend(graph[node] - visited)

        members = [results[i] for i in sorted(group)]
        count = len(members)

        if count >= 4:
            dup_status = "trending"
        elif count >= 2:
            dup_status = "popular"
        else:
            dup_status = "unique"

        # Помечаем дубликаты (score > 0.85): каноном остаётся член пары с бóльшим
        # total_score, а не просто более ранний по индексу
        duplicates = set()
        for i, j, score in pairs:
            if i in group and j in group and score > 0.85:
                si = results[i].get("total_score", 0)
                sj = results[j].get("total_score", 0)
                duplicates.add(j if sj <= si else i)

        groups.append({
            "status": dup_status,
            "members": members,
            "duplicate_indices": list(duplicates),
        })

    return groups
