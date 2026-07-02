"""Build SEO keyword surfaces from the multilingual lexicon and splice them into
the engine.

Edits (data only, engine logic untouched):
- nlp/entities.py : CONCEPT_ENTITIES / PERSON_ENTITIES / PLATFORM_ENTITIES literals
- checks/viral_score.py : VIRAL_TRIGGERS dict, _DEDUP_PREFIXES set

Also writes niches/seo/keywords.yaml (the clean niche artifact).
Run from anywhere; paths resolved relative to repo root.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))   # ContentCombine/
RAW = os.path.join(HERE, "keywords_raw")
NICHE = os.path.abspath(os.path.join(HERE, ".."))               # niches/seo/

# ---- load lexicon -------------------------------------------------------
terms = []
for f in ("lex_en.json", "lex_ru.json", "lex_intl.json"):
    for t in json.load(open(os.path.join(RAW, f), encoding="utf-8")):
        t["term"] = t["term"].strip().lower()
        terms.append(t)

# ---- routing tables -----------------------------------------------------
BRAND_PLATFORM = {
    "google", "bing", "yandex", "baidu", "naver", "duckduckgo",
    "chatgpt", "openai", "gemini", "claude", "perplexity", "copilot", "grok",
    "llama", "mistral", "chatgpt search", "gptbot", "google-extended",
    "claudebot", "perplexitybot", "нейро", "поиск с алисой", "yandexgpt", "алиса ai",
}
SUPER_S = {"google", "chatgpt", "ai overviews", "yandex", "perplexity", "gemini", "openai"}
NAMED_ENTITIES = {  # proper-noun algorithms / filters / surfaces -> entity boost
    "panda", "penguin", "hummingbird", "bert", "mum", "rankbrain", "pigeon",
    "medic update", "минусинск", "баден-баден", "агс", "андромеда", "вега",
    "палех", "королёв", "yati", "y1", "оригами", "тайфун",
    "ai overviews", "ai mode", "sge",
}
PERSON_SET = {
    "john mueller", "gary illyes", "danny sullivan", "search liaison",
    "martin splitt", "search central", "judge mehta",
    "мюллер", "джон мюллер", "илльес", "гэри илльес", "search console",
}
# trigger category <- lexicon category
TRIG_MAP = {
    "algo-update": "algoupd", "penalty": "penalty", "penalty-filter": "penalty",
    "ranking-volatility": "volatility", "ranking": "ranking",
    "urgency-impact": "urgency", "technical": "technical", "link": "link",
    "content": "content", "measurement": "measure",
    "ai-search": "aisearch", "product-ai-search": "aisearch",
}
WEIGHTS = {
    "penalty": {"high": 45, "med": 22, "low": 10},
    "algoupd": {"high": 40, "med": 20, "low": 8},
    "volatility": {"high": 38, "med": 18, "low": 7},
    "aisearch": {"high": 30, "med": 15, "low": 6},
    "urgency": {"high": 20, "med": 10, "low": 4},
    "technical": {"high": 20, "med": 10, "low": 4},
    "link": {"high": 18, "med": 9, "low": 4},
    "content": {"high": 16, "med": 8, "low": 3},
    "ranking": {"high": 10, "med": 6, "low": 3},
    "measure": {"high": 10, "med": 6, "low": 3},
}
TIER_FREQ = {"S": 100, "A": 70, "B": 40, "C": 15}
TIER_FROM = {"high": "A", "med": "B", "low": "C"}

# ---- partition into entities + triggers ---------------------------------
platform, studio, game = {}, {}, {}      # entity buckets: key -> tier
triggers = {}                            # (cat,tier) -> set(keywords)


def add_entity(bucket, key, tier):
    if key in SUPER_S:
        tier = "S"
    # keep strongest tier if seen twice
    order = {"S": 3, "A": 2, "B": 1, "C": 0}
    if key not in bucket or order[tier] > order[bucket[key]]:
        bucket[key] = tier


for t in terms:
    term, cat, tier_word = t["term"], t["category"], t["tier"]
    etier = TIER_FROM.get(tier_word, "C")

    # --- entity assignment (one bucket, priority platform > studio > game) ---
    if term in BRAND_PLATFORM or cat in ("search-engine", "ai-platform"):
        add_entity(platform, term, etier)
    elif cat in ("google-people", "google-ru", "yandex-product") and term in PERSON_SET:
        add_entity(studio, term, etier)
    elif cat in ("google-ru", "yandex-product"):
        add_entity(studio, term, etier)
    elif cat in ("standard", "schema-standards"):
        add_entity(game, term, etier)
    elif term in NAMED_ENTITIES:
        add_entity(game, term, etier)
    elif cat in ("product-ai-search", "ai-search"):
        add_entity(game, term, etier)

    # --- trigger assignment ---
    tcat = None
    if cat == "google-people":
        if term not in PERSON_SET:
            tcat = "urgency"          # google confirms / leak / antitrust ...
    elif cat in TRIG_MAP:
        tcat = TRIG_MAP[cat]
    if tcat and len(term) >= 4:       # avoid short-substring false positives
        triggers.setdefault((tcat, tier_word), set()).add(term)

# ---- build python literals ----------------------------------------------
def entity_literal(name, bucket):
    lines = [f"{name} = {{"]
    for key in sorted(bucket):
        tier = bucket[key]
        val = {"tier": tier, "freq": TIER_FREQ[tier], "aliases": []}
        lines.append(f"    {json.dumps(key, ensure_ascii=False)}: {json.dumps(val, ensure_ascii=False)},")
    lines.append("}")
    return "\n".join(lines)


def triggers_literal():
    lines = ["VIRAL_TRIGGERS = {"]
    for (tcat, tier_word) in sorted(triggers):
        kws = sorted(triggers[(tcat, tier_word)])
        w = WEIGHTS[tcat][tier_word]
        tid = f"{tcat}_{tier_word}"
        label = f"{tcat}/{tier_word}"
        lines.append(f"    {json.dumps(tid)}: {{")
        lines.append(f"        \"label\": {json.dumps(label)},")
        lines.append(f"        \"weight\": {w},")
        lines.append(f"        \"keywords\": {json.dumps(kws, ensure_ascii=False)},")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


dedup_prefixes = sorted({tcat for (tcat, _) in triggers})

# ---- splice into engine files -------------------------------------------
def splice(path, pattern, replacement, flags=re.DOTALL):
    src = open(path, encoding="utf-8").read()
    new, n = re.subn(pattern, lambda m: replacement, src, count=1, flags=flags)
    assert n == 1, f"pattern not found in {path}: {pattern[:40]}"
    open(path, "w", encoding="utf-8").write(new)


ge = os.path.join(ROOT, "nlp", "entities.py")
vs = os.path.join(ROOT, "checks", "viral_score.py")

splice(ge, r"^CONCEPT_ENTITIES = \{.*?\n\}", entity_literal("CONCEPT_ENTITIES", game), re.DOTALL | re.MULTILINE)
splice(ge, r"^PERSON_ENTITIES = \{.*?\n\}", entity_literal("PERSON_ENTITIES", studio), re.DOTALL | re.MULTILINE)
splice(ge, r"^PLATFORM_ENTITIES = \{.*?\n\}", entity_literal("PLATFORM_ENTITIES", platform), re.DOTALL | re.MULTILINE)

splice(vs, r"^VIRAL_TRIGGERS = \{.*?\n\}", triggers_literal(), re.DOTALL | re.MULTILINE)

# _DEDUP_PREFIXES (indented inside a function) — edit text directly
set_literal = "{" + ", ".join(json.dumps(p) for p in dedup_prefixes) + "}"
src = open(vs, encoding="utf-8").read()
src, n1 = re.subn(r"_DEDUP_PREFIXES = \{[^}]*\}", "_DEDUP_PREFIXES = " + set_literal, src, count=1)
assert n1 == 1, f"dedup={n1}"
open(vs, "w", encoding="utf-8").write(src)

# ---- niche artifact: keywords.yaml --------------------------------------
def yaml_block():
    out = ["# SEO niche virality keywords (generated from research/keywords_raw/*)",
           "# Two surfaces: entities (tier S/A/B/C -> boost) + triggers (weighted events).",
           "entities:"]
    for name, bucket in (("search_surfaces", platform), ("brands_tools_people", studio), ("topics_algos", game)):
        out.append(f"  {name}:")
        for k in sorted(bucket):
            out.append(f"    - {{term: {json.dumps(k, ensure_ascii=False)}, tier: {bucket[k]}}}")
    out.append("triggers:")
    for (tcat, tier_word) in sorted(triggers):
        kws = sorted(triggers[(tcat, tier_word)])
        out.append(f"  - id: {tcat}_{tier_word}")
        out.append(f"    weight: {WEIGHTS[tcat][tier_word]}")
        out.append(f"    keywords: {json.dumps(kws, ensure_ascii=False)}")
    return "\n".join(out)

open(os.path.join(NICHE, "keywords.yaml"), "w", encoding="utf-8").write(yaml_block())

# ---- report -------------------------------------------------------------
print("ENTITIES: platform=%d studio=%d topics=%d" % (len(platform), len(studio), len(game)))
print("TRIGGERS: %d (cat,tier) groups; categories=%s" % (len(triggers), dedup_prefixes))
tk = sum(len(v) for v in triggers.values())
print("trigger keywords total:", tk)
print("spliced: entities.py, viral_score.py ; wrote keywords.yaml")
