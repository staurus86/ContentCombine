"""
Proxy rotation, retry logic with exponential backoff, and circuit breaker
for HTML/RSS parsers.
"""

import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Round-robin counter for proxy rotation (thread-safe)
_proxy_index = 0
_proxy_lock = threading.Lock()


def get_proxy() -> str | None:
    """Return next proxy URL via round-robin, or None if no proxies configured."""
    global _proxy_index
    proxies = _get_proxy_list()
    if not proxies:
        return None
    with _proxy_lock:
        proxy = proxies[_proxy_index % len(proxies)]
        _proxy_index += 1
    return proxy


def get_proxies_dict() -> dict | None:
    """Return {"http": proxy, "https": proxy} for requests, or None if no proxies configured."""
    proxy = get_proxy()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


# Common browser User-Agent strings for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
]

# Circuit breaker state: domain -> {"failures": int, "blocked_until": datetime}
_circuit_breaker: dict[str, dict] = {}

# Max consecutive failures before blocking a domain
CIRCUIT_BREAKER_THRESHOLD = 5
# How long to block a domain after threshold is reached
CIRCUIT_BREAKER_COOLDOWN = timedelta(hours=1)


def _get_proxy_list() -> list[str]:
    """Load proxy list from PROXY_LIST env var (imported at call time to avoid circular imports)."""
    from config import PROXY_LIST
    if not PROXY_LIST:
        return []
    return [p.strip() for p in PROXY_LIST.split(",") if p.strip()]


def _get_random_ua() -> str:
    """Return a random User-Agent string."""
    from config import USER_AGENT_ROTATE
    if USER_AGENT_ROTATE:
        return random.choice(USER_AGENTS)
    return USER_AGENTS[0]


def _get_domain(url: str) -> str:
    """Extract domain from URL for circuit breaker tracking."""
    return urlparse(url).netloc


def _is_domain_blocked(domain: str) -> bool:
    """Check if domain is blocked by circuit breaker."""
    state = _circuit_breaker.get(domain)
    if not state:
        return False
    if state["failures"] >= CIRCUIT_BREAKER_THRESHOLD:
        if datetime.now(timezone.utc) < state["blocked_until"]:
            return True
        # Cooldown expired, reset
        del _circuit_breaker[domain]
        return False
    return False


def _record_failure(domain: str):
    """Record a failure for circuit breaker."""
    if domain not in _circuit_breaker:
        _circuit_breaker[domain] = {"failures": 0, "blocked_until": datetime.now(timezone.utc)}
    _circuit_breaker[domain]["failures"] += 1
    if _circuit_breaker[domain]["failures"] >= CIRCUIT_BREAKER_THRESHOLD:
        _circuit_breaker[domain]["blocked_until"] = datetime.now(timezone.utc) + CIRCUIT_BREAKER_COOLDOWN
        logger.warning("Circuit breaker OPEN for domain %s — skipping for 1 hour", domain)


def _record_success(domain: str):
    """Reset failure counter on success."""
    if domain in _circuit_breaker:
        del _circuit_breaker[domain]


def get_session(proxy_url: str = None) -> requests.Session:
    """
    Return a requests.Session configured with an optional proxy and random User-Agent.
    If proxy_url is None and proxies are configured, pick a random one.
    If no proxies configured, return a plain session.
    """
    session = requests.Session()
    session.headers["User-Agent"] = _get_random_ua()

    proxies = _get_proxy_list()
    chosen = proxy_url
    if chosen is None and proxies:
        chosen = random.choice(proxies)

    if chosen:
        session.proxies = {
            "http": chosen,
            "https": chosen,
        }

    return session


def fetch_with_retry(url: str, max_retries: int = 3, timeout: int = 10) -> requests.Response:
    """
    Fetch a URL with retry logic, proxy rotation, and circuit breaker.

    - Exponential backoff: 2s, 4s, 8s (with jitter)
    - On 429/503 or connection error: rotate proxy and retry
    - Circuit breaker: skip domain after 5 consecutive failures for 1 hour

    Returns a Response object on success.
    Raises the last exception if all retries fail.
    """
    domain = _get_domain(url)

    if _is_domain_blocked(domain):
        raise ConnectionError(f"Circuit breaker OPEN: domain {domain} is blocked after {CIRCUIT_BREAKER_THRESHOLD} consecutive failures")

    proxies = _get_proxy_list()
    last_exception = None
    used_proxies = set()

    for attempt in range(max_retries):
        # Pick a proxy (rotate on retry)
        proxy_url = None
        if proxies:
            available = [p for p in proxies if p not in used_proxies]
            if not available:
                available = proxies  # reset if we've tried all
            proxy_url = random.choice(available)
            used_proxies.add(proxy_url)

        session = get_session(proxy_url)

        try:
            resp = session.get(url, timeout=timeout)

            if resp.status_code in (429, 503):
                logger.warning(
                    "Got %d from %s (attempt %d/%d), rotating proxy and retrying",
                    resp.status_code, domain, attempt + 1, max_retries,
                )
                _wait_backoff(attempt)
                last_exception = requests.HTTPError(
                    f"HTTP {resp.status_code}", response=resp
                )
                continue

            resp.raise_for_status()
            _record_success(domain)
            return resp

        except (requests.ConnectionError, requests.Timeout, ConnectionError) as e:
            logger.warning(
                "Connection error fetching %s (attempt %d/%d): %s",
                url, attempt + 1, max_retries, e,
            )
            last_exception = e
            _wait_backoff(attempt)
            continue

        except requests.HTTPError as e:
            # Non-retryable HTTP errors (4xx except 429)
            _record_failure(domain)
            raise

        finally:
            session.close()

    # All retries exhausted
    _record_failure(domain)
    if last_exception:
        raise last_exception
    raise ConnectionError(f"Failed to fetch {url} after {max_retries} retries")


def _wait_backoff(attempt: int):
    """Exponential backoff with jitter: 2s, 4s, 8s base + random 0-1s."""
    base = 2 ** (attempt + 1)  # 2, 4, 8
    jitter = random.uniform(0, 1)
    delay = base + jitter
    logger.debug("Backoff: sleeping %.1fs before retry", delay)
    time.sleep(delay)
