import hashlib
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from nlp.game_entities import find_entities


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()


def exact_duplicate(title1: str, title2: str) -> bool:
    return hashlib.md5(normalize(title1).encode()).hexdigest() == \
           hashlib.md5(normalize(title2).encode()).hexdigest()


def entity_overlap(text1: str, text2: str) -> float:
    """Пересечение игровых сущностей между двумя текстами (через единую базу)."""
    ents1 = set(e["name"] for e in find_entities(text1))
    ents2 = set(e["name"] for e in find_entities(text2))
    if not ents1 and not ents2:
        return 0
    return len(ents1 & ents2) / max(len(ents1 | ents2), 1)


def tfidf_similarity(titles: list[str], texts: list[str] | None = None) -> list[tuple]:
    """Комбинированная похожесть: TF-IDF (0.6) + entity overlap (0.4).

    Оптимизировано: entity results кешируются, не пересчитываются в O(n²) цикле.
    """
    if len(titles) < 2:
        return []

    # TF-IDF cosine similarity по заголовкам
    vectorizer = TfidfVectorizer(ngram_range=(1, 2))
    matrix = vectorizer.fit_transform([normalize(t) for t in titles])
    sim = cosine_similarity(matrix)

    compare_texts = texts if texts else titles

    # Предвычисляем entity sets для всех текстов (O(n) вместо O(n²))
    ent_sets = []
    for t in compare_texts:
        ent_sets.append(set(e["name"] for e in find_entities(t)))

    pairs = []
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            tfidf_score = sim[i][j]

            # Быстрый entity overlap из предвычисленных sets
            e1, e2 = ent_sets[i], ent_sets[j]
            if not e1 and not e2:
                ent_score = 0
            else:
                ent_score = len(e1 & e2) / max(len(e1 | e2), 1)

            combined = 0.6 * tfidf_score + 0.4 * ent_score

            if combined > 0.35:
                pairs.append((i, j, round(float(combined), 2)))
    return pairs


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

        # Помечаем дубликаты (score > 0.85)
        duplicates = set()
        for i, j, score in pairs:
            if i in group and j in group and score > 0.85:
                duplicates.add(j)

        groups.append({
            "status": dup_status,
            "members": members,
            "duplicate_indices": list(duplicates),
        })

    return groups
