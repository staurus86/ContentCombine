"""Скрипт для снятия реальной частоты из Keys.so для базы game_entities.

Запуск: python scripts/fetch_entity_freq.py [--dry-run]

Что делает:
1. Запрашивает ws (search volume) по каждой сущности через Keys.so API
2. Нормализует ws -> freq (0-100)
3. Сохраняет кеш результатов в scripts/entity_freq_cache.json
4. Автоматически обновляет freq значения в nlp/game_entities.py (если не --dry-run)

ВНИМАНИЕ: тратит API Keys.so (~150 запросов). Запускать 1 раз.

Флаги:
  --dry-run   Только показать результаты, не изменять файлы
"""

import sys
import os
import re
import time
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from apis.keyso import get_keyword_info
from nlp.game_entities import GAME_ENTITIES, STUDIO_ENTITIES, PLATFORM_ENTITIES

ENTITY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "nlp", "game_entities.py",
)
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "entity_freq_cache.json")


def fetch_all() -> dict:
    """Запрашивает ws для всех сущностей через Keys.so API.

    Returns:
        dict: {entity_key: {"name": str, "ws": int, "category": str}}
    """
    results = {}

    all_entities = {}
    for k in GAME_ENTITIES:
        all_entities[f"game:{k}"] = (k, "game")
    for k in STUDIO_ENTITIES:
        all_entities[f"studio:{k}"] = (k, "studio")
    for k in PLATFORM_ENTITIES:
        all_entities[f"platform:{k}"] = (k, "platform")

    total = len(all_entities)
    print(f"Total entities to check: {total}")

    errors = 0
    for i, (key, (name, category)) in enumerate(all_entities.items()):
        print(f"[{i+1}/{total}] {name}...", end=" ", flush=True)
        try:
            info = get_keyword_info(name)
            if info is None:
                raise ValueError("API returned None")
            ws = info.get("ws", 0)
            results[key] = {"name": name, "ws": ws, "category": category}
            print(f"ws={ws}")
        except Exception as e:
            print(f"ERROR: {e}")
            results[key] = {"name": name, "ws": 0, "category": category}
            errors += 1
            # Если слишком много ошибок подряд — возможно API упал
            if errors >= 10:
                print(f"\nТoo many consecutive errors ({errors}). API may be down.")
                print("Partial results will be saved.")
                break

        time.sleep(1.5)  # Rate limit

    return results


def normalize_results(results: dict) -> dict:
    """Нормализует ws -> freq (0-100).

    Returns:
        dict: {entity_name: freq_value}  e.g. {"gta 6": 100, "minecraft": 95, ...}
    """
    # Нормализуем ws -> freq (0-100) отдельно для каждой категории
    freq_map = {}

    for category in ("game", "studio", "platform"):
        cat_entries = {k: v for k, v in results.items() if v["category"] == category}
        ws_values = [v["ws"] for v in cat_entries.values() if v["ws"] > 0]
        if not ws_values:
            continue
        max_ws = max(ws_values)
        for key, data in cat_entries.items():
            if data["ws"] > 0:
                freq = max(5, round(data["ws"] / max_ws * 100))
            else:
                freq = 5  # Минимум для сущностей без данных
            freq_map[data["name"]] = freq

    return freq_map


def update_entity_file(freq_map: dict) -> int:
    """Обновляет freq значения в nlp/game_entities.py.

    Args:
        freq_map: {entity_name: new_freq_value}

    Returns:
        int: количество обновлённых сущностей
    """
    with open(ENTITY_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    updated = 0
    # Паттерн: "entity_name": {"tier": "X", "freq": NN,
    # Заменяем только значение freq
    for name, new_freq in freq_map.items():
        # Экранируем спецсимволы в имени для regex
        escaped_name = re.escape(name)
        pattern = rf'("{escaped_name}":\s*\{{"tier":\s*"[SABC]",\s*"freq":\s*)(\d+)(,)'
        match = re.search(pattern, content)
        if match:
            old_freq = int(match.group(2))
            if old_freq != new_freq:
                content = content[:match.start(2)] + str(new_freq) + content[match.end(2):]
                updated += 1

    with open(ENTITY_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    return updated


def save_cache(results: dict, freq_map: dict):
    """Сохраняет результаты в кеш-файл для справки."""
    cache_data = {
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "raw_results": results,
        "normalized_freq": freq_map,
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)
    print(f"Cache saved to {CACHE_FILE}")


def print_results(results: dict, freq_map: dict):
    """Печатает результаты в удобном формате."""
    print("\n--- Normalized freq values (sorted by freq) ---")
    for name, freq in sorted(freq_map.items(), key=lambda x: x[1], reverse=True):
        # Находим ws
        ws = 0
        for v in results.values():
            if v["name"] == name:
                ws = v["ws"]
                break
        print(f'  "{name}": freq={freq} (ws={ws})')


def main():
    parser = argparse.ArgumentParser(description="Fetch entity frequencies from Keys.so")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print results, don't modify game_entities.py")
    args = parser.parse_args()

    if not config.KEYSO_API_KEY:
        print("ERROR: KEYSO_API_KEY not set in config/environment")
        sys.exit(1)

    print("Fetching entity frequencies from Keys.so API...")
    if args.dry_run:
        print("(DRY RUN — files will not be modified)\n")

    results = fetch_all()

    if not results:
        print("No results fetched. Exiting.")
        sys.exit(1)

    freq_map = normalize_results(results)
    print_results(results, freq_map)

    # Всегда сохраняем кеш
    save_cache(results, freq_map)

    if args.dry_run:
        print("\n[DRY RUN] Skipping file update.")
    else:
        updated = update_entity_file(freq_map)
        print(f"\nUpdated {updated} freq values in {ENTITY_FILE}")


if __name__ == "__main__":
    main()
