"""Merge + dedupe raw SEO source research into a master source database.

Reads raw/*.json (engine-format source dicts from 10 research agents),
deduplicates across categories, and emits:
- sources.master.json   (deduped, full metadata)
- sources.master.md     (human-readable, grouped by category)
- sources.engine.json   (minimal dicts ready to drop into a niche SOURCES list)
"""
import glob
import html
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")

FEED_SUFFIXES = ["/feed.xml", "/rss.xml", "/index.xml", "/feed/", "/feed",
                 "/rss/", "/rss", "?format=rss", "/feed.atom", ".rdf"]


def norm_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.rstrip("/")
    for suf in FEED_SUFFIXES:
        if u.endswith(suf):
            u = u[: -len(suf)]
            break
    return u.rstrip("/")


def dedup_key(s: dict) -> str:
    t = s.get("type")
    if t == "telegram":
        return "tg:" + s.get("channel", "").strip().lower()
    if t == "bluesky":
        return "bs:" + s.get("handle", "").strip().lower()
    # url-based types: use the effective feed url where available
    eff = s.get("rss_url") or s.get("url") or ""
    return "url:" + norm_url(eff)


TYPE_RANK = {"rss": 0, "telegram": 0, "bluesky": 0, "homepage": 1, "sitemap": 2}


def main():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.json")))
    raw = []
    per_file = {}
    for f in files:
        items = json.load(open(f, encoding="utf-8"))
        per_file[os.path.basename(f)] = len(items)
        for s in items:
            # unescape any HTML entities that leaked into text fields
            for k in ("name", "notes"):
                if isinstance(s.get(k), str):
                    s[k] = html.unescape(s[k])
            raw.append(s)

    groups = {}
    for s in raw:
        groups.setdefault(dedup_key(s), []).append(s)

    master = []
    for key, cands in groups.items():
        cands.sort(key=lambda x: (not x.get("rss_verified", False),
                                  TYPE_RANK.get(x.get("type"), 9)))
        best = dict(cands[0])
        cats = sorted({c.get("category", "") for c in cands if c.get("category")})
        best["categories"] = cats
        best["category"] = cats[0] if cats else best.get("category", "")
        best["rss_verified"] = any(c.get("rss_verified") for c in cands)
        best["interval"] = min(c.get("interval", 120) for c in cands)
        best["dup_count"] = len(cands)
        master.append(best)

    # stable, readable order: category then name
    master.sort(key=lambda x: (x.get("category", ""), x.get("name", "").lower()))

    # --- write master.json ---
    json.dump(master, open(os.path.join(HERE, "sources.master.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # --- write engine-ready minimal dicts ---
    engine = []
    for s in master:
        e = {"name": s["name"], "type": s["type"]}
        for k in ("url", "rss_url", "channel", "handle", "url_filter"):
            if s.get(k):
                e[k] = s[k]
        e["interval"] = s["interval"]
        engine.append(e)
    json.dump(engine, open(os.path.join(HERE, "sources.engine.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # --- stats ---
    total = len(master)
    raw_total = len(raw)
    by_cat, by_lang, by_type = {}, {}, {}
    verified = 0
    for s in master:
        by_cat[s["category"]] = by_cat.get(s["category"], 0) + 1
        by_lang[s.get("lang", "?")] = by_lang.get(s.get("lang", "?"), 0) + 1
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1
        if s.get("rss_verified"):
            verified += 1

    # --- write markdown ---
    md = []
    md.append("# SEO Sources — Master Database\n")
    md.append(f"**Собрано (сырые):** {raw_total} · **После дедупа:** {total} · "
              f"**RSS подтверждён:** {verified} ({round(100*verified/total)}%)\n")
    md.append("## По категориям\n")
    for c, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        md.append(f"- `{c}`: {n}")
    md.append("\n## По языкам\n")
    for c, n in sorted(by_lang.items(), key=lambda x: -x[1]):
        md.append(f"- `{c}`: {n}")
    md.append("\n## По типу источника\n")
    for c, n in sorted(by_type.items(), key=lambda x: -x[1]):
        md.append(f"- `{c}`: {n}")
    md.append("\n## Полный список\n")
    cur = None
    for s in master:
        if s["category"] != cur:
            cur = s["category"]
            md.append(f"\n### {cur}\n")
        loc = s.get("url") or ("t.me/" + s.get("channel", "")) or s.get("handle", "")
        v = "✅" if s.get("rss_verified") else "⚠️"
        md.append(f"- {v} **{s['name']}** (`{s['type']}`, {s.get('lang','?')}, {s['interval']}m) — {loc}")
    open(os.path.join(HERE, "sources.master.md"), "w", encoding="utf-8").write("\n".join(md))

    # --- console report ---
    print("RAW per file:")
    for f, n in per_file.items():
        print(f"  {f}: {n}")
    print(f"\nRAW total: {raw_total}")
    print(f"DEDUPED total: {total}  (removed {raw_total - total} duplicates)")
    print(f"RSS verified: {verified} ({round(100*verified/total)}%)")
    print("\nBy type:", by_type)
    print("By lang:", dict(sorted(by_lang.items(), key=lambda x: -x[1])))
    print("By category:")
    for c, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")


if __name__ == "__main__":
    main()
