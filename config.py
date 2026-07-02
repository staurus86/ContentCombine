import logging
import os
from dotenv import load_dotenv

load_dotenv()


def _int_env(key: str, default: int) -> int:
    """Safe int parsing from env var with fallback on invalid values."""
    val = os.getenv(key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        logging.warning("Invalid value '%s' for %s, using default %d", val, key, default)
        return default

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
KEYSO_API_KEY = os.getenv("KEYSO_API_KEY", "")

# Google Sheets
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SHEETS_TAB = "Лист1"
SHEETS_TAB_READY = os.getenv("SHEETS_TAB_READY", "Ready")
SHEETS_TAB_NOT_READY = os.getenv("SHEETS_TAB_NOT_READY", "NotReady")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///news.db")

# LLM
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
# Fallback models tried in order if the primary times out / errors (e.g. Cloudflare
# flaps on the gateway). Comma-separated env override.
LLM_FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "gpt-4o,gpt-4.1").split(",") if m.strip()]
# Daily LLM spend cap in USD for the full-auto pipeline. 0 = disabled (no cap).
LLM_DAILY_CAP_USD = float(os.getenv("LLM_DAILY_CAP_USD", "0"))

# Automation thresholds
AUTO_APPROVE_THRESHOLD = _int_env("AUTO_APPROVE_THRESHOLD", 0)  # 0 = disabled, use pipeline buttons
AUTO_REWRITE_ON_PUBLISH_NOW = os.getenv("AUTO_REWRITE_ON_PUBLISH_NOW", "true").lower() == "true"
AUTO_REWRITE_STYLE = os.getenv("AUTO_REWRITE_STYLE", "news")

# Scoring formula weights (must sum to 1.0).
# Viral снижен с 0.2 до 0.05: виральность уже входит в internal (total_score —
# среднее 4 чеков, включая viral), с 0.2 она учитывалась дважды.
SCORE_WEIGHT_INTERNAL = float(os.getenv("SCORE_WEIGHT_INTERNAL", "0.55"))
SCORE_WEIGHT_VIRAL = float(os.getenv("SCORE_WEIGHT_VIRAL", "0.05"))
SCORE_WEIGHT_KEYSO = float(os.getenv("SCORE_WEIGHT_KEYSO", "0.15"))
SCORE_WEIGHT_TRENDS = float(os.getenv("SCORE_WEIGHT_TRENDS", "0.1"))
SCORE_WEIGHT_HEADLINE = float(os.getenv("SCORE_WEIGHT_HEADLINE", "0.15"))

# Pipeline thresholds
FULL_AUTO_SCORE_THRESHOLD = _int_env("FULL_AUTO_SCORE_THRESHOLD", 70)
FULL_AUTO_FINAL_THRESHOLD = _int_env("FULL_AUTO_FINAL_THRESHOLD", 60)
AUTO_EXPORT_THRESHOLD = _int_env("AUTO_EXPORT_THRESHOLD", 60)
AUTO_REJECT_SCORE_THRESHOLD = _int_env("AUTO_REJECT_SCORE_THRESHOLD", 15)
PUBLISH_SPACING_MINUTES = _int_env("PUBLISH_SPACING_MINUTES", 15)

# LLM settings
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
LLM_TIMEOUT_SECONDS = _int_env("LLM_TIMEOUT_SECONDS", 45)

# Viral thresholds
VIRAL_HIGH_THRESHOLD = _int_env("VIRAL_HIGH_THRESHOLD", 70)
VIRAL_MEDIUM_THRESHOLD = _int_env("VIRAL_MEDIUM_THRESHOLD", 40)
VIRAL_LOW_THRESHOLD = _int_env("VIRAL_LOW_THRESHOLD", 20)

# Freshness: новости старше N дней (по дате статьи) уезжают в «Удалённые»
# (soft-delete, не физически). Применяется и при сборе, и в фоновой джобе.
NEWS_MAX_AGE_DAYS = _int_env("NEWS_MAX_AGE_DAYS", 45)

# Retention periods
DELETED_NEWS_RETENTION_DAYS = _int_env("DELETED_NEWS_RETENTION_DAYS", 30)
PLAINTEXT_RETENTION_DAYS = _int_env("PLAINTEXT_RETENTION_DAYS", 7)
HEALTH_LOG_RETENTION_DAYS = _int_env("HEALTH_LOG_RETENTION_DAYS", 7)

# Cron schedule (hours, 0-23)
AUTO_RESCORE_CRON_HOUR = _int_env("AUTO_RESCORE_CRON_HOUR", 4)
AUTO_DIGEST_CRON_HOUR = _int_env("AUTO_DIGEST_CRON_HOUR", 23)
STORYLINES_EXPORT_CRON_HOUR = _int_env("STORYLINES_EXPORT_CRON_HOUR", 9)

# Batch sizes
SHEETS_BATCH_SIZE = _int_env("SHEETS_BATCH_SIZE", 25)
NEWS_BATCH_FETCH_LIMIT = _int_env("NEWS_BATCH_FETCH_LIMIT", 20)
VK_POST_MAX_AGE_DAYS = _int_env("VK_POST_MAX_AGE_DAYS", 7)
VK_POSTS_BATCH_SIZE = _int_env("VK_POSTS_BATCH_SIZE", 20)
TELEGRAM_POST_MAX_AGE_DAYS = _int_env("TELEGRAM_POST_MAX_AGE_DAYS", 7)
TELEGRAM_MESSAGES_BATCH_SIZE = _int_env("TELEGRAM_MESSAGES_BATCH_SIZE", 20)
# RSS freshness window. Was hardcoded 7d — too tight for low-cadence feeds (Moz,
# Yoast, Backlinko publish every 2-4 weeks), which then never delivered while
# homepage sources at the same cadence did (their window is 30-45d). Aligned to 30.
RSS_POST_MAX_AGE_DAYS = _int_env("RSS_POST_MAX_AGE_DAYS", 30)

# System health
WATCHDOG_STALE_TIMEOUT = _int_env("WATCHDOG_STALE_TIMEOUT", 300)
SOURCE_FAILURE_THRESHOLD = _int_env("SOURCE_FAILURE_THRESHOLD", 5)
# Источник считается "мёртвым" по времени молчания, а не по "0 за 24ч":
# редко публикующие официальные источники не должны висеть как dead.
SOURCE_DEAD_DAYS = _int_env("SOURCE_DEAD_DAYS", 14)

# Адаптивная частота автономного парсинга: есть активный логин — чаще, нет — реже.
PARSE_ACTIVE_MIN = _int_env("PARSE_ACTIVE_MIN", 15)          # цикл при активном логине
PARSE_IDLE_MIN = _int_env("PARSE_IDLE_MIN", 60)             # цикл когда никто не залогинен
PARSE_ACTIVE_WINDOW_MIN = _int_env("PARSE_ACTIVE_WINDOW_MIN", 20)  # «логин активен», если запрос был в эти минуты
SOURCE_PROBE_COOLDOWN = _int_env("SOURCE_PROBE_COOLDOWN", 600)
ZOMBIE_THREADS_CRITICAL = _int_env("ZOMBIE_THREADS_CRITICAL", 5)

# Sheets API tuning
SHEETS_MIN_API_INTERVAL = float(os.getenv("SHEETS_MIN_API_INTERVAL", "1.2"))
SHEETS_CLIENT_TTL = _int_env("SHEETS_CLIENT_TTL", 3000)

# Proxy & User-Agent rotation
PROXY_LIST = os.getenv("PROXY_LIST", "")
USER_AGENT_ROTATE = os.getenv("USER_AGENT_ROTATE", "true").lower() == "true"

# Keys.so
KEYSO_REGION = os.getenv("KEYSO_REGION", "ru")
KEYSO_BASE_URL = "https://api.keys.so/api/v2"

# Русскоязычные источники (Keys.so region=ru, остальные — us)
RU_SOURCES = {"StopGame", "DTF", "Playground", "iXBT.games", "VGTimes"}

def keyso_region_for_source(source: str) -> str:
    """Возвращает регион Keys.so по источнику."""
    return "ru" if source in RU_SOURCES else "us"

# Google Trends regions
REGIONS = ["RU", "US", "GB", "DE"]

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "")  # comma-separated authorized chat IDs
TELEGRAM_NOTIFY_THRESHOLD = _int_env("TELEGRAM_NOTIFY_THRESHOLD", 70)
# Channel for one-click publishing of a news post (e.g. "@my_seo_channel" or "-100...").
# Empty => publish endpoint returns no_token and UI copies the post to clipboard.
TELEGRAM_PUBLISH_CHANNEL = os.getenv("TELEGRAM_PUBLISH_CHANNEL", "")
# Private admin chat for operational alerts (critical incidents, mass source failure).
TELEGRAM_ALERT_CHAT = os.getenv("TELEGRAM_ALERT_CHAT", "")

# VK API
VK_API_TOKEN = os.getenv("VK_API_TOKEN", "")
VK_API_VERSION = "5.199"

# Telegram Channel Parser (Telethon)
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "igronews_tg")

# Sources — flat list, scheduler distributes by interval
SOURCES = [
    {"name": "Anthropic News", "type": "homepage", "url": "https://www.anthropic.com/news", "interval": 120},
    {"name": "AWS Machine Learning Blog", "type": "rss", "url": "https://aws.amazon.com/blogs/machine-learning/feed/", "interval": 30},
    {"name": "Cohere Blog", "type": "homepage", "url": "https://cohere.com/blog", "interval": 60},
    {"name": "DeepMind on blog.google", "type": "rss", "url": "https://blog.google/technology/google-deepmind/rss/", "interval": 60},
    {"name": "Google AI blog.google", "type": "rss", "url": "https://blog.google/technology/ai/rss/", "interval": 30},
    {"name": "Google DeepMind Blog", "type": "rss", "url": "https://deepmind.google/blog/rss.xml", "interval": 60},
    {"name": "Google Developers Blog", "type": "homepage", "url": "https://developers.googleblog.com/en/", "interval": 60},
    {"name": "Google Gemini blog", "type": "rss", "url": "https://blog.google/products/gemini/rss/", "interval": 30},
    {"name": "Google Research Blog", "type": "rss", "url": "https://research.google/blog/rss/", "interval": 60},
    {"name": "Hugging Face Blog", "type": "rss", "url": "https://huggingface.co/blog/feed.xml", "interval": 30},
    {"name": "Meta Research", "type": "rss", "url": "https://research.facebook.com/feed/", "interval": 120},
    {"name": "Microsoft Cloud Blog", "type": "rss", "url": "https://www.microsoft.com/en-us/microsoft-cloud/blog/feed/", "interval": 60},
    {"name": "Microsoft Research Blog", "type": "rss", "url": "https://www.microsoft.com/en-us/research/feed/", "interval": 60},
    {"name": "Mistral AI News", "type": "homepage", "url": "https://mistral.ai/news", "interval": 60},
    {"name": "NVIDIA Deep Learning Blog", "type": "rss", "url": "https://blogs.nvidia.com/blog/category/deep-learning/feed/", "interval": 1440},
    {"name": "NVIDIA Technical Blog", "type": "rss", "url": "https://developer.nvidia.com/blog/feed/", "interval": 30},
    {"name": "Official Microsoft Blog", "type": "rss", "url": "https://blogs.microsoft.com/feed/", "interval": 30},
    {"name": "OpenAI News", "type": "rss", "url": "https://openai.com/news/rss.xml", "interval": 30},    {"name": "Qwen Blog", "type": "rss", "url": "https://qwenlm.github.io/blog/index.xml", "interval": 60},
    {"name": "Sber on Habr", "type": "rss", "url": "https://habr.com/ru/rss/companies/sberbank/articles/", "interval": 60},
    {"name": "SberDevices on Habr", "type": "rss", "url": "https://habr.com/ru/rss/companies/sberdevices/articles/", "interval": 60},
    {"name": "Stability AI News", "type": "rss", "url": "https://stability.ai/news-updates?format=rss", "interval": 120},
    {"name": "Together AI Blog", "type": "rss", "url": "https://www.together.ai/blog/rss.xml", "interval": 60},
    {"name": "xAI Blog", "type": "homepage", "url": "https://x.ai/news", "interval": 60},
    {"name": "Yandex on Habr", "type": "rss", "url": "https://habr.com/ru/rss/companies/yandex/articles/", "interval": 60},
    {"name": "Aleyda SEO Blog", "type": "rss", "url": "https://www.aleydasolis.com/en/blog/feed/", "interval": 60},
    {"name": "AthenaHQ", "type": "homepage", "url": "https://www.athenahq.ai/blog", "interval": 60},
    {"name": "Clearscope Blog", "type": "homepage", "url": "https://www.clearscope.io/blog", "interval": 60},
    {"name": "Cloudflare Blog", "type": "rss", "url": "https://blog.cloudflare.com/rss/", "interval": 30},
    {"name": "Common Crawl", "type": "rss", "url": "https://commoncrawl.org/blog/rss.xml", "interval": 60},    {"name": "Growth Memo", "type": "rss", "url": "https://www.growth-memo.com/feed", "interval": 60},
    {"name": "iPullRank", "type": "rss", "url": "https://ipullrank.com/feed", "interval": 60},
    {"name": "Lily Ray", "type": "homepage", "url": "https://www.lilyray.nyc/blog", "rss_url": "https://lilyray.nyc/feed/", "interval": 1440},
    {"name": "Marie Haynes", "type": "rss", "url": "https://www.mariehaynes.com/feed/", "interval": 60},
    {"name": "Originality AI", "type": "homepage", "url": "https://originality.ai/blog", "interval": 60},
    {"name": "Otterly AI", "type": "homepage", "url": "https://otterly.ai/blog/", "interval": 60},
    {"name": "Peec AI", "type": "homepage", "url": "https://peec.ai/blog", "interval": 60},
    {"name": "Profound", "type": "homepage", "url": "https://tryprofound.com/blog", "interval": 60},
    {"name": "Rankscale", "type": "homepage", "url": "https://www.rankscale.ai/blog", "interval": 60},
    {"name": "Schema.org Blog", "type": "homepage", "url": "https://blog.schema.org/", "interval": 60},
    {"name": "Scrunch", "type": "homepage", "url": "https://scrunch.com/blog", "interval": 60},
    {"name": "Tom Critchlow", "type": "rss", "url": "https://tomcritchlow.com/feed.xml", "interval": 60},
    {"name": "W3C News", "type": "rss", "url": "https://www.w3.org/blog/news/feed/", "interval": 60},
    {"name": "Bing Search Blog", "type": "rss", "url": "https://blogs.bing.com/search/feed", "interval": 60},
    {"name": "Bing Webmaster Blog", "type": "rss", "url": "https://blogs.bing.com/webmaster/feed", "interval": 1440},
    {"name": "Brave Blog", "type": "rss", "url": "https://brave.com/blog/index.xml", "interval": 60},
    {"name": "Coc Coc Blog", "type": "rss", "url": "https://blog.coccoc.com/feed/", "interval": 120},
    {"name": "DuckDuckGo Blog", "type": "rss", "url": "https://spreadprivacy.com/rss/", "interval": 1440},
    {"name": "Ecosia Blog", "type": "rss", "url": "https://blog.ecosia.org/rss/", "interval": 120},
    {"name": "Google Keyword Search", "type": "rss", "url": "https://blog.google/products/search/rss/", "interval": 60},
    {"name": "Google Search Central", "type": "rss", "url": "https://developers.google.com/search/blog/feed.xml", "interval": 60},
    {"name": "Google Search Central YouTube", "type": "rss", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCWf2ZlNsCGDS89VBF_awNvA", "interval": 120},
    {"name": "Google Search Status", "type": "rss", "url": "https://status.search.google.com/en/feed.atom", "interval": 30},
    {"name": "Kagi Blog", "type": "rss", "url": "https://blog.kagi.com/rss.xml", "interval": 120},
    {"name": "Mojeek Blog", "type": "rss", "url": "https://blog.mojeek.com/feed.xml", "interval": 120},
    {"name": "The Keyword (Google)", "type": "rss", "url": "https://blog.google/rss/", "interval": 30},
    {"name": "Yandex Webmaster RU", "type": "rss", "url": "https://webmaster.yandex.ru/blog/rss/", "interval": 60},
    {"name": "BS:aleyda", "type": "bluesky", "handle": "aleyda.bsky.social", "interval": 60},
    {"name": "BS:britneymuller", "type": "bluesky", "handle": "britneymuller.bsky.social", "interval": 60},
    {"name": "BS:crystalcarter", "type": "bluesky", "handle": "crystalontheweb.bsky.social", "interval": 60},
    {"name": "BS:cyrusshepard", "type": "bluesky", "handle": "zyppy.com", "interval": 60},
    {"name": "BS:dannysullivan", "type": "bluesky", "handle": "dannysullivan.bsky.social", "interval": 60},
    {"name": "BS:garyillyes", "type": "bluesky", "handle": "methode.bsky.social", "interval": 60},
    {"name": "BS:gfiorelli", "type": "bluesky", "handle": "gfiorelli1.bsky.social", "interval": 60},
    {"name": "BS:glenngabe", "type": "bluesky", "handle": "glenngabe.bsky.social", "interval": 60},
    {"name": "BS:jonoalderson", "type": "bluesky", "handle": "jono.id", "interval": 60},
    {"name": "BS:joost", "type": "bluesky", "handle": "joost.blog", "interval": 60},
    {"name": "BS:kevinindig", "type": "bluesky", "handle": "kevin-indig.bsky.social", "interval": 60},
    {"name": "BS:lilyray", "type": "bluesky", "handle": "lilyray.nyc", "interval": 60},
    {"name": "BS:mariehaynes", "type": "bluesky", "handle": "mariehaynes.bsky.social", "interval": 60},
    {"name": "BS:mordy", "type": "bluesky", "handle": "mordyoberstein.bsky.social", "interval": 60},
    {"name": "BS:nickleroy", "type": "bluesky", "handle": "nickleroy.com", "interval": 60},
    {"name": "BS:olesia", "type": "bluesky", "handle": "fajela.bsky.social", "interval": 60},    {"name": "BS:randfish", "type": "bluesky", "handle": "randfish.bsky.social", "interval": 60},
    {"name": "BS:searchliaison", "type": "bluesky", "handle": "searchliaison.bsky.social", "interval": 60},
    {"name": "BS:seroundtable", "type": "bluesky", "handle": "seroundtable.bsky.social", "interval": 60},    {"name": "BS:tomcapper", "type": "bluesky", "handle": "tcapper.co.uk", "interval": 60},
    {"name": "BS:tomcritchlow", "type": "bluesky", "handle": "tomcritchlow.com", "interval": 60},
    {"name": "BS:womenintechseo", "type": "bluesky", "handle": "womenintechseo.bsky.social", "interval": 60},
    {"name": "Ahrefs Blog", "type": "rss", "url": "https://ahrefs.com/blog/feed/", "interval": 60},
    {"name": "Backlinko", "type": "rss", "url": "https://backlinko.com/feed", "interval": 120},
    {"name": "Detailed", "type": "rss", "url": "https://detailed.com/feed/", "interval": 1440},
    {"name": "GreenLane Marketing", "type": "rss", "url": "https://www.greenlanemarketing.com/feed/", "interval": 120},
    {"name": "GSQi", "type": "rss", "url": "https://www.gsqi.com/marketing-blog/feed/", "interval": 60},
    {"name": "MarTech", "type": "rss", "url": "https://martech.org/feed/", "interval": 30},
    {"name": "Niche Pursuits", "type": "rss", "url": "https://www.nichepursuits.com/feed/", "interval": 60},
    {"name": "Onely Blog", "type": "rss", "url": "https://www.onely.com/feed/", "interval": 60},
    {"name": "Practical Ecommerce", "type": "rss", "url": "https://www.practicalecommerce.com/feed", "interval": 60},
    {"name": "Rank Math Blog", "type": "rss", "url": "https://rankmath.com/blog/feed/", "interval": 60},
    {"name": "SE Ranking Blog", "type": "rss", "url": "https://seranking.com/blog/feed/", "interval": 60},
    {"name": "Search Engine Journal", "type": "rss", "url": "https://www.searchenginejournal.com/feed/", "interval": 30},
    {"name": "Search Engine Land", "type": "rss", "url": "https://searchengineland.com/feed", "interval": 30},
    {"name": "Search Engine People", "type": "rss", "url": "https://www.searchenginepeople.com/feed", "interval": 60},
    {"name": "Search Engine Roundtable", "type": "rss", "url": "https://www.seroundtable.com/index.rdf", "interval": 30},
    {"name": "Search Engine Watch", "type": "rss", "url": "https://www.searchenginewatch.com/feed/", "interval": 1440},
    {"name": "Search Logistics", "type": "rss", "url": "https://searchlogistics.com/feed/", "interval": 120},
    {"name": "Search Off the Record", "type": "rss", "url": "https://rss.libsyn.com/shows/266492/destinations/2027951.xml", "interval": 120},
    {"name": "Semrush Blog", "type": "rss", "url": "https://www.semrush.com/blog/feed/", "interval": 60},
    {"name": "SISTRIX EN", "type": "rss", "url": "https://www.sistrix.com/feed/", "interval": 60},
    {"name": "Stan Ventures Blog", "type": "rss", "url": "https://www.stanventures.com/blog/feed/", "interval": 60},    {"name": "Victorious Blog", "type": "rss", "url": "https://victorious.com/blog/feed/", "interval": 120},
    {"name": "Yoast SEO Blog", "type": "rss", "url": "https://yoast.com/feed/", "interval": 60},
    {"name": "DE: 121WATT", "type": "rss", "url": "https://www.121watt.de/feed/", "interval": 60},
    {"name": "DE: Aufgesang (Olaf Kopp)", "type": "rss", "url": "https://www.aufgesang.de/feed/", "interval": 60},
    {"name": "DE: OMT.de", "type": "rss", "url": "https://www.omt.de/feed/", "interval": 1440},
    {"name": "DE: SISTRIX Blog", "type": "homepage", "url": "https://www.sistrix.de/news/", "rss_url": "https://www.sistrix.de/news/feed/", "interval": 60},
    {"name": "DE: t3n SEO/Digital", "type": "rss", "url": "https://t3n.de/rss.xml", "interval": 30},
    {"name": "ES: Ahrefs Blog (ES)", "type": "rss", "url": "https://ahrefs.com/blog/es/feed/", "interval": 60},
    {"name": "ES: Cyberclick", "type": "homepage", "url": "https://www.cyberclick.es/blog", "rss_url": "https://www.cyberclick.es/numerical-blog/rss.xml", "interval": 120},
    {"name": "ES: IEDGE", "type": "rss", "url": "https://www.iedge.eu/feed", "interval": 1440},
    {"name": "ES: MJ Cachon", "type": "rss", "url": "https://mjcachon.com/feed/", "interval": 60},
    {"name": "ES: Semrush Blog (ES)", "type": "rss", "url": "https://es.semrush.com/blog/feed/", "interval": 1440},
    {"name": "ES: useo.es", "type": "rss", "url": "https://www.useo.es/feed/", "interval": 1440},
    {"name": "FR: Abondance", "type": "rss", "url": "https://www.abondance.com/feed", "interval": 30},
    {"name": "FR: Blog du Moderateur (BDM)", "type": "rss", "url": "https://www.blogdumoderateur.com/feed/", "interval": 30},
    {"name": "IT: SEOZoom", "type": "rss", "url": "https://www.seozoom.it/feed/", "interval": 30},
    {"name": "IT: Studio Samo", "type": "rss", "url": "https://www.studiosamo.it/feed/", "interval": 1440},
    {"name": "JA: Web Tan Forum", "type": "rss", "url": "https://webtan.impress.co.jp/rss.xml", "interval": 30},
    {"name": "NL: Frankwatching", "type": "rss", "url": "https://www.frankwatching.com/feed/", "interval": 30},
    {"name": "PL: Sprawny Marketing", "type": "rss", "url": "https://sprawnymarketing.pl/feed/", "interval": 60},
    {"name": "PT: Conversion.com.br (BR)", "type": "rss", "url": "https://www.conversion.com.br/blog/feed/", "interval": 60},
    {"name": "PT: RD Station Blog (BR)", "type": "rss", "url": "https://blog.rdstation.com/feed/", "interval": 1440},
    {"name": "RU: Cossa", "type": "rss", "url": "https://www.cossa.ru/rss/", "interval": 30},    {"name": "RU: Habr — SEO", "type": "rss", "url": "https://habr.com/ru/rss/hub/seo/all/", "interval": 30},
    {"name": "RU: Labrika Блог", "type": "sitemap", "url": "https://labrika.ru/sitemap.xml", "url_filter": "/blog/", "interval": 120},
    {"name": "RU: ppc.world", "type": "rss", "url": "https://ppc.world/feed/", "interval": 60},
    {"name": "RU: Roem", "type": "rss", "url": "https://roem.ru/feed/", "interval": 30},
    {"name": "RU: Rush Analytics Блог", "type": "rss", "url": "https://www.rush-analytics.ru/blog/feed", "interval": 1440},
    {"name": "RU: SEOnews", "type": "rss", "url": "https://www.seonews.ru/rss/all/", "interval": 30},
    {"name": "RU: Texterra Блог", "type": "rss", "url": "https://texterra.ru/blog/rss/", "interval": 60},
    {"name": "RU: Шакин (Михаил Шакин)", "type": "rss", "url": "https://shakin.ru/feed", "interval": 1440},
        {"name": "Leadership in SEO", "type": "rss", "url": "https://www.leadershipinseo.com/feed", "interval": 60},
    {"name": "Lenny's Newsletter", "type": "rss", "url": "https://www.lennysnewsletter.com/feed", "interval": 60},
    {"name": "Near Media", "type": "rss", "url": "https://www.nearmedia.co/feed/", "interval": 60},
    {"name": "SEOFOMO", "type": "homepage", "url": "https://hub.seofomo.co/", "interval": 60},
    {"name": "Siege Media", "type": "rss", "url": "https://www.siegemedia.com/feed", "interval": 60},
    {"name": "SparkToro", "type": "rss", "url": "https://sparktoro.com/blog/feed/", "interval": 60},
    {"name": "The Neuron", "type": "rss", "url": "https://www.theneuron.ai/feed", "interval": 30},
    {"name": "TLDR Marketing", "type": "rss", "url": "https://tldr.tech/api/rss/marketing", "interval": 30},
    {"name": "Whitespark", "type": "rss", "url": "https://whitespark.ca/feed/", "interval": 60},
    {"name": "TG:ARSENKIN TOOLS", "type": "telegram", "channel": "arsenkin_ru", "interval": 60},
    {"name": "TG:CRAFT SEO", "type": "telegram", "channel": "craftseo", "interval": 60},
    {"name": "TG:Devaka Talk", "type": "telegram", "channel": "devakatalk", "interval": 60},
    {"name": "TG:Digital-бар", "type": "telegram", "channel": "digital_bar", "interval": 60},
    {"name": "TG:im_for_all", "type": "telegram", "channel": "im_for_all", "interval": 60},
    {"name": "TG:InMarketing (Ingate)", "type": "telegram", "channel": "beINmarketing", "interval": 60},
    {"name": "TG:Keys.so", "type": "telegram", "channel": "keysso_public", "interval": 60},
    {"name": "TG:Kistankin SEO", "type": "telegram", "channel": "SeoKistankin", "interval": 60},
    {"name": "TG:noindex, nofollow", "type": "telegram", "channel": "notjohnmu", "interval": 60},
    {"name": "TG:PR-CY News", "type": "telegram", "channel": "prcynews", "interval": 60},
    {"name": "TG:PromoPult", "type": "telegram", "channel": "promopultru", "interval": 60},
    {"name": "TG:Rookee о маркетинге", "type": "telegram", "channel": "rookee_ru", "interval": 60},
    {"name": "TG:SEalytics", "type": "telegram", "channel": "sealytics", "interval": 60},
    {"name": "TG:Semantica", "type": "telegram", "channel": "semantica", "interval": 60},
    {"name": "TG:SEO Specialist", "type": "telegram", "channel": "seospecialist", "interval": 60},
    {"name": "TG:SEO без воды", "type": "telegram", "channel": "bez_seo", "interval": 60},
    {"name": "TG:SEO от Ant-Team", "type": "telegram", "channel": "seoantteam", "interval": 60},
    {"name": "TG:SEOforge", "type": "telegram", "channel": "seoforge", "interval": 60},
    {"name": "TG:WillDay", "type": "telegram", "channel": "willday1", "interval": 60},
    {"name": "TG:АлаичЪ про SEO", "type": "telegram", "channel": "alaevseo", "interval": 60},
    {"name": "TG:Высоков SEO", "type": "telegram", "channel": "vysokoffru", "interval": 60},
    {"name": "TG:Дежурный SEO-доктор", "type": "telegram", "channel": "siteclinic_doctor", "interval": 60},
    {"name": "TG:Ленивый сеошник", "type": "telegram", "channel": "markin_seo", "interval": 60},
    {"name": "TG:О SEO по делу (Бирюков)", "type": "telegram", "channel": "biryukovseo", "interval": 60},
    {"name": "TG:Пиксель Тулс", "type": "telegram", "channel": "pixeltools", "interval": 60},
    {"name": "TG:Сосновский SEO", "type": "telegram", "channel": "sosnovskij", "interval": 60},
    {"name": "TG:Топвизор", "type": "telegram", "channel": "topvisor", "interval": 60},
    {"name": "TG:Топвизор-Журнал", "type": "telegram", "channel": "topvisorjournal", "interval": 60},
    {"name": "TG:Честное SEO", "type": "telegram", "channel": "seomikhaylov", "interval": 60},
    {"name": "TG:Шакин", "type": "telegram", "channel": "shakinru", "interval": 60},
    {"name": "TG:Яндекс Вебмастер", "type": "telegram", "channel": "yandexwebm", "interval": 60},
    {"name": "TG:DrMax SEO", "type": "telegram", "channel": "drmaxseo", "interval": 60},
    {"name": "TG:Oleg Shestakov", "type": "telegram", "channel": "oshestakovdigital", "interval": 60},
    {"name": "TG:Максим Котёнков", "type": "telegram", "channel": "seokotenkov", "interval": 60},
    {"name": "TG:SEO Анны Ященко", "type": "telegram", "channel": "seoandme", "interval": 60},
    {"name": "TG:Как надо SEO", "type": "telegram", "channel": "ulianoov", "interval": 60},
    {"name": "TG:SEO-секретики", "type": "telegram", "channel": "seosekretiki", "interval": 60},
    {"name": "TG:Hello, Digital World", "type": "telegram", "channel": "HelloDigitalWorld", "interval": 60},
    {"name": "TG:Ozhgibesov Agency", "type": "telegram", "channel": "ozhgibesov_agency", "interval": 60},
    {"name": "TG:Заметки по SEO (Soltyk)", "type": "telegram", "channel": "soltykseo", "interval": 60},
    {"name": "TG:Хорошие SEOшники", "type": "telegram", "channel": "good_seoguys", "interval": 60},
    {"name": "TG:Борминцев Александр", "type": "telegram", "channel": "bortalks", "interval": 60},
    {"name": "TG:Tokarev Digital", "type": "telegram", "channel": "tokarevdigital", "interval": 60},
    {"name": "TG:Олег Саламаха", "type": "telegram", "channel": "gelo_seo", "interval": 60},
    {"name": "TG:prolinki (Павлов)", "type": "telegram", "channel": "prolinki", "interval": 60},
    {"name": "TG:Семантист", "type": "telegram", "channel": "semantist_channel", "interval": 60},
    {"name": "TG:Shishkin SEO", "type": "telegram", "channel": "shishkinseo", "interval": 60},
    {"name": "AccuRanker Blog", "type": "homepage", "url": "https://www.accuranker.com/blog/", "rss_url": "https://www.accuranker.com/rss-blog.xml", "interval": 90},
    {"name": "Advanced Web Ranking Blog", "type": "homepage", "url": "https://www.advancedwebranking.com/blog", "interval": 120},
    {"name": "Botify Blog", "type": "homepage", "url": "https://www.botify.com/blog", "interval": 90},
    {"name": "BuzzStream Blog", "type": "rss", "url": "https://buzzstream.com/blog/feed/", "interval": 90},
    {"name": "cognitiveSEO Blog", "type": "rss", "url": "https://cognitiveseo.com/blog/feed/", "interval": 120},
    {"name": "Diib Learn", "type": "rss", "url": "https://diib.com/learn/feed/", "interval": 90},
    {"name": "Frase Blog", "type": "homepage", "url": "https://www.frase.io/blog/", "interval": 90},
    {"name": "JetOctopus Blog", "type": "rss", "url": "http://jetoctopus.com/feed/", "interval": 90},
    {"name": "LinkResearchTools Blog", "type": "homepage", "url": "https://www.linkresearchtools.com/blog/", "rss_url": "https://www.linkresearchtools.com/blog/feed/", "interval": 120},
    {"name": "LowFruits Blog", "type": "rss", "url": "https://lowfruits.io/blog/feed/", "interval": 120},
    {"name": "Lumar Blog", "type": "rss", "url": "https://www.lumar.io/blog/feed/", "interval": 90},
    {"name": "Majestic Blog", "type": "rss", "url": "https://blog.majestic.com/feed/", "interval": 120},
    {"name": "Mangools Blog", "type": "rss", "url": "https://mangools.com/blog/feed/", "interval": 90},
    {"name": "MarketMuse Blog", "type": "rss", "url": "https://blog.marketmuse.com/rss/", "interval": 1440},
    {"name": "Neil Patel Blog", "type": "rss", "url": "https://neilpatel.com/blog/feed/", "interval": 90},
    {"name": "Nightwatch Blog", "type": "homepage", "url": "https://nightwatch.io/blog/", "interval": 120},
    {"name": "Oncrawl Blog", "type": "rss", "url": "https://www.oncrawl.com/feed/", "interval": 90},
    {"name": "Respona Blog", "type": "homepage", "url": "https://respona.com/blog/", "rss_url": "https://respona.com/feed/", "interval": 90},
    {"name": "Ryte Magazine", "type": "homepage", "url": "https://en.ryte.com/magazine/", "interval": 120},
    {"name": "Screaming Frog Blog", "type": "rss", "url": "https://www.screamingfrog.co.uk/feed/", "interval": 90},
    {"name": "seoClarity Blog", "type": "rss", "url": "https://www.seoclarity.net/blog/rss.xml", "interval": 90},
    {"name": "Serpstat Blog", "type": "homepage", "url": "https://serpstat.com/blog/", "rss_url": "https://serpstat.com/feed/", "interval": 90},
    {"name": "SISTRIX Blog", "type": "homepage", "url": "https://www.sistrix.com/blog/", "rss_url": "https://www.sistrix.com/blog/feed/", "interval": 120},
    {"name": "Sitebulb Resources", "type": "sitemap", "url": "https://sitebulb.com/sitemap.xml", "url_filter": "/resources/", "interval": 90},
    {"name": "Sitechecker Blog", "type": "homepage", "url": "https://sitechecker.pro/blog/", "rss_url": "https://sitechecker.pro/feed/", "interval": 90},
    {"name": "SpyFu Blog", "type": "rss", "url": "https://www.spyfu.com/blog/feed/", "interval": 90},
    {"name": "Surfer SEO Blog", "type": "rss", "url": "https://surferseo.com/blog/rss.xml", "interval": 90},
    {"name": "Wincher Blog", "type": "homepage", "url": "https://www.wincher.com/blog", "interval": 90},
    # --- Добор 2026-06-23: GEO/AEO, AI-поиск, agentic, AI-видимость бренда (проверены парсером) ---
    {"name": "The Decoder", "type": "rss", "url": "https://the-decoder.com/feed/", "interval": 60},
    {"name": "Press Gazette", "type": "rss", "url": "https://pressgazette.co.uk/feed/", "interval": 60},
    {"name": "TechCrunch AI", "type": "rss", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "interval": 60},
    {"name": "VentureBeat AI", "type": "rss", "url": "https://venturebeat.com/category/ai/feed/", "interval": 60},
    {"name": "Simon Willison", "type": "rss", "url": "https://simonwillison.net/atom/everything/", "interval": 60},
    {"name": "MIT Technology Review AI", "type": "rss", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed/", "interval": 90},
    {"name": "Sterling Sky", "type": "rss", "url": "https://www.sterlingsky.ca/feed/", "interval": 120},
    {"name": "Lily Ray Substack", "type": "rss", "url": "https://lilyraynyc.substack.com/feed", "interval": 120},
    {"name": "Addy Osmani", "type": "rss", "url": "https://addyosmani.com/rss.xml", "interval": 120},
    {"name": "WordLift Blog", "type": "rss", "url": "https://wordlift.io/blog/en/feed/", "interval": 90},
    {"name": "Writesonic GEO", "type": "rss", "url": "https://writesonic.com/blog/rss.xml", "interval": 90},
    {"name": "semai.ai", "type": "rss", "url": "https://semai.ai/blogs/feed/", "interval": 60},    {"name": "Suganthan Mohanadasan", "type": "homepage", "url": "https://suganthan.com/blog", "interval": 120},
    {"name": "No Hacks", "type": "homepage", "url": "https://nohacks.co/", "interval": 120},
    {"name": "ZipTie", "type": "homepage", "url": "https://ziptie.dev/blog/", "interval": 120},
    {"name": "Superlines", "type": "homepage", "url": "https://www.superlines.io/articles/", "interval": 120},
    {"name": "Brandlight", "type": "homepage", "url": "https://www.brandlight.ai/blog/", "interval": 120},
    {"name": "Trakkr", "type": "homepage", "url": "https://trakkr.ai/", "interval": 120},
    {"name": "BrightEdge", "type": "homepage", "url": "https://www.brightedge.com/blog", "interval": 120},
    {"name": "Bluefish AI", "type": "homepage", "url": "https://www.bluefishai.com/", "interval": 120}
]


def load_persistent_settings():
    """Load settings from DB, overriding env defaults. Called after init_db()."""
    try:
        from storage.database import get_all_app_settings
        settings = get_all_app_settings()
        if not settings:
            return

        import config
        # Override config values from DB if they exist
        _str_keys = {
            "GOOGLE_SHEETS_ID": "GOOGLE_SHEETS_ID",
            "GOOGLE_SERVICE_ACCOUNT_JSON": "GOOGLE_SERVICE_ACCOUNT_JSON",
            "SHEETS_TAB": "SHEETS_TAB",
            "SHEETS_TAB_READY": "SHEETS_TAB_READY",
            "SHEETS_TAB_NOT_READY": "SHEETS_TAB_NOT_READY",
            "OPENAI_API_KEY": "OPENAI_API_KEY",
            "KEYSO_API_KEY": "KEYSO_API_KEY",
            "VK_API_TOKEN": "VK_API_TOKEN",
            "TELEGRAM_BOT_TOKEN": "TELEGRAM_BOT_TOKEN",
            "LLM_MODEL": "LLM_MODEL",
            "TELEGRAM_PUBLISH_CHANNEL": "TELEGRAM_PUBLISH_CHANNEL",
            "KEYSO_REGION": "KEYSO_REGION",
            "AUTO_REWRITE_STYLE": "AUTO_REWRITE_STYLE",
            "OPENAI_API_KEY": "OPENAI_API_KEY",
            "OPENAI_BASE_URL": "OPENAI_BASE_URL",
        }
        for db_key, attr in _str_keys.items():
            if db_key in settings and settings[db_key]:
                setattr(config, attr, settings[db_key])

        # Int settings
        for key in (
            "AUTO_APPROVE_THRESHOLD", "TELEGRAM_NOTIFY_THRESHOLD",
            "FULL_AUTO_SCORE_THRESHOLD", "FULL_AUTO_FINAL_THRESHOLD",
            "AUTO_EXPORT_THRESHOLD", "AUTO_REJECT_SCORE_THRESHOLD",
            "PUBLISH_SPACING_MINUTES", "LLM_TIMEOUT_SECONDS",
            "VIRAL_HIGH_THRESHOLD", "VIRAL_MEDIUM_THRESHOLD", "VIRAL_LOW_THRESHOLD",
            "DELETED_NEWS_RETENTION_DAYS", "PLAINTEXT_RETENTION_DAYS", "HEALTH_LOG_RETENTION_DAYS",
            "AUTO_RESCORE_CRON_HOUR", "AUTO_DIGEST_CRON_HOUR", "STORYLINES_EXPORT_CRON_HOUR",
            "SHEETS_BATCH_SIZE", "NEWS_BATCH_FETCH_LIMIT",
            "VK_POST_MAX_AGE_DAYS", "VK_POSTS_BATCH_SIZE",
            "TELEGRAM_POST_MAX_AGE_DAYS", "TELEGRAM_MESSAGES_BATCH_SIZE",
            "WATCHDOG_STALE_TIMEOUT", "SOURCE_FAILURE_THRESHOLD",
            "SOURCE_PROBE_COOLDOWN", "ZOMBIE_THREADS_CRITICAL",
            "SHEETS_CLIENT_TTL",
        ):
            if key in settings:
                try:
                    setattr(config, key, int(settings[key]))
                except (ValueError, TypeError):
                    pass

        # Bool settings
        if "AUTO_REWRITE_ON_PUBLISH_NOW" in settings:
            setattr(config, "AUTO_REWRITE_ON_PUBLISH_NOW", settings["AUTO_REWRITE_ON_PUBLISH_NOW"].lower() == "true")

        # Float settings
        for key in ("SCORE_WEIGHT_INTERNAL", "SCORE_WEIGHT_VIRAL", "SCORE_WEIGHT_KEYSO",
                     "SCORE_WEIGHT_TRENDS", "SCORE_WEIGHT_HEADLINE",
                     "LLM_TEMPERATURE", "SHEETS_MIN_API_INTERVAL"):
            if key in settings:
                try:
                    setattr(config, key, float(settings[key]))
                except (ValueError, TypeError):
                    pass

        # Reload sheets client if sheets config changed
        if any(k in settings for k in ("GOOGLE_SHEETS_ID", "GOOGLE_SERVICE_ACCOUNT_JSON")):
            try:
                import storage.sheets as sheets_mod
                sheets_mod._client = None  # Force re-auth on next use
            except Exception:
                pass

        # Load saved prompts into LLM module
        try:
            import apis.llm as llm
            prompt_keys = {
                "PROMPT_TREND_FORECAST": "PROMPT_TREND_FORECAST",
                "PROMPT_MERGE_ANALYSIS": "PROMPT_MERGE_ANALYSIS",
                "PROMPT_KEYSO_QUERIES": "PROMPT_KEYSO_QUERIES",
                "PROMPT_REWRITE": "PROMPT_REWRITE",
            }
            for db_key, attr in prompt_keys.items():
                if db_key in settings and settings[db_key]:
                    setattr(llm, attr, settings[db_key])
            if "REWRITE_STYLES" in settings:
                import json
                saved_styles = json.loads(settings["REWRITE_STYLES"])
                for style_name, instructions in saved_styles.items():
                    if style_name in llm.REWRITE_STYLES:
                        llm.REWRITE_STYLES[style_name]["instructions"] = instructions
        except Exception:
            pass

        logging.getLogger(__name__).info("Loaded %d persistent settings from DB", len(settings))
    except Exception as e:
        logging.getLogger(__name__).debug("No persistent settings loaded: %s", e)
