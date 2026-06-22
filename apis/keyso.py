import logging
import requests
import config

logger = logging.getLogger(__name__)

HEADERS = {"Content-Type": "application/json"}


def _make_request(endpoint: str, params: dict, region: str = None) -> dict | None:
    """Выполняет запрос к Keys.so API."""
    import time as _t
    params["auth-token"] = config.KEYSO_API_KEY
    if "base" not in params:
        params["base"] = region or config.KEYSO_REGION
    t0 = _t.time()
    try:
        url = f"{config.KEYSO_BASE_URL}{endpoint}"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        latency_ms = int((_t.time() - t0) * 1000)
        # Track API cost (best-effort)
        try:
            from core.observability import track_api_call
            track_api_call("keyso", endpoint=endpoint, latency_ms=latency_ms)
        except Exception:
            pass
        return resp.json()
    except Exception as e:
        latency_ms = int((_t.time() - t0) * 1000)
        try:
            from core.observability import track_api_call
            track_api_call("keyso", endpoint=endpoint, latency_ms=latency_ms,
                           status="error", error_message=str(e)[:200])
        except Exception:
            pass
        logger.error("Keys.so API error (%s): %s", endpoint, e)
        return None


def get_keyword_info(keyword: str, region: str = None) -> dict:
    """Получает частотность ключевого слова (с кэшем 24ч)."""
    from apis.cache import cache_get, cache_set, cache_key, rate_check, rate_increment
    ck = cache_key("keyso_info", keyword, region or config.KEYSO_REGION)
    cached = cache_get(ck)
    if cached is not None:
        return cached
    if not rate_check("keyso"):
        logger.warning("Keys.so rate limit exceeded")
        return {"ws": 0, "wsk": 0}
    rate_increment("keyso")
    data = _make_request("/report/simple/keyword_dashboard", {"keyword": keyword}, region=region)
    if not data:
        return {"ws": 0, "wsk": 0}
    result = {
        "ws": data.get("ws", 0),
        "wsk": data.get("wsk", 0),
    }
    cache_set(ck, result)
    return result


def get_similar_keywords(keyword: str, limit: int = 10, region: str = None) -> list[dict]:
    """Получает похожие поисковые запросы (с кэшем 24ч)."""
    from apis.cache import cache_get, cache_set, cache_key, rate_check, rate_increment
    ck = cache_key("keyso_similar", keyword, limit, region or config.KEYSO_REGION)
    cached = cache_get(ck)
    if cached is not None:
        return cached
    if not rate_check("keyso"):
        logger.warning("Keys.so rate limit exceeded")
        return []
    rate_increment("keyso")
    data = _make_request("/report/simple/similarkeys", {
        "keyword": keyword,
        "per_page": limit,
    }, region=region)
    if not data or "data" not in data:
        return []
    result = [
        {
            "word": item.get("word", ""),
            "ws": item.get("ws", 0),
            "wsk": item.get("wsk", 0),
            "cnt": item.get("cnt", 0),
        }
        for item in data["data"][:limit]
    ]
    cache_set(ck, result)
    return result


def check_keywords_bulk(keywords: list[str]) -> dict:
    """Массовая проверка частотности списка ключевых слов (с кэшем 24ч)."""
    if not keywords:
        return {}

    from apis.cache import cache_get, cache_set, cache_key
    # Cache by sorted keyword list
    ck = cache_key("keyso_bulk", ",".join(sorted(keywords[:20])))
    cached = cache_get(ck)
    if cached is not None:
        return cached

    try:
        # Создаём задачу
        resp = requests.post(
            f"{config.KEYSO_BASE_URL}/tools/keywords_by_list",
            params={"auth-token": config.KEYSO_API_KEY},
            json={"list": keywords, "base": config.KEYSO_REGION},
            timeout=15,
        )
        resp.raise_for_status()
        uid = resp.json().get("uid")
        if not uid:
            return {}

        # Получаем результат с retry
        import time
        result_data = {}
        for attempt in range(3):
            time.sleep(3 + attempt * 2)  # 3s, 5s, 7s
            try:
                result_resp = requests.get(
                    f"{config.KEYSO_BASE_URL}/tools/keywords_by_list/{uid}",
                    params={"token": config.KEYSO_API_KEY, "per_page": len(keywords)},
                    timeout=15,
                )
                result_resp.raise_for_status()
                result_data = result_resp.json()
                if result_data.get("data"):
                    break
            except Exception:
                if attempt == 2:
                    raise
                continue

        result = {
            item["word"]: {"ws": item.get("ws", 0), "wsk": item.get("wsk", 0)}
            for item in result_data.get("data", [])
        }
        cache_set(ck, result, ttl=86400)  # 24h
        return result
    except Exception as e:
        logger.error("Keys.so bulk check error: %s", e)
        return {}
