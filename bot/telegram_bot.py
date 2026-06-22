"""Telegram bot for IgroNews editorial notifications and quick actions.

Uses python-telegram-bot v20+ (async).
Single-file bot: commands, inline keyboards, notification helpers.
"""

import logging
import asyncio
import threading
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_app: Application | None = None
_loop: asyncio.AbstractEventLoop | None = None


def _get_authorized_chat_ids() -> set[int]:
    """Return set of authorized chat IDs from config."""
    raw = getattr(config, "TELEGRAM_CHAT_IDS", "")
    if not raw:
        return set()
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def _is_authorized(chat_id: int) -> bool:
    allowed = _get_authorized_chat_ids()
    # If no IDs configured, allow anyone (first-use convenience)
    return len(allowed) == 0 or chat_id in allowed


def _db_query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a read query and return list of dicts."""
    from storage.database import get_connection, _is_postgres
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        if _is_postgres():
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        else:
            return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register chat for notifications and show welcome message."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"IgroNews Bot\n"
        f"Your chat_id: {chat_id}\n\n"
        f"Add this ID to TELEGRAM_CHAT_IDS env var to receive notifications.\n\n"
        f"Commands:\n"
        f"/top - Top-5 news by score today\n"
        f"/stats - Today's statistics\n"
        f"/digest - 24h digest of top news"
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top-5 highest scored news today with approve/reject buttons."""
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("Access denied.")
        return

    from storage.database import _is_postgres
    ph = "%s" if _is_postgres() else "?"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sql = (
        f"SELECT n.id, n.title, n.source, n.status, na.total_score "
        f"FROM news n LEFT JOIN news_analysis na ON n.id = na.news_id "
        f"WHERE n.parsed_at >= {ph} AND na.total_score IS NOT NULL "
        f"ORDER BY na.total_score DESC LIMIT 5"
    )
    rows = _db_query(sql, (today,))

    if not rows:
        await update.message.reply_text("No scored news today.")
        return

    for i, row in enumerate(rows, 1):
        score = row.get("total_score", 0) or 0
        status = row.get("status", "?")
        source = row.get("source", "?")
        title = (row.get("title") or "No title")[:120]
        news_id = row["id"]

        text = f"*{i}. [{score}pts] {source}*\n{title}\nStatus: {status}"

        buttons = []
        if status not in ("approved", "processed", "ready", "rejected", "duplicate"):
            buttons = [
                InlineKeyboardButton("Approve", callback_data=f"approve:{news_id}"),
                InlineKeyboardButton("Reject", callback_data=f"reject:{news_id}"),
            ]

        reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=reply_markup
        )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's stats."""
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("Access denied.")
        return

    from storage.database import _is_postgres
    ph = "%s" if _is_postgres() else "?"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sql = (
        f"SELECT status, COUNT(*) as cnt FROM news "
        f"WHERE parsed_at >= {ph} GROUP BY status ORDER BY cnt DESC"
    )
    rows = _db_query(sql, (today,))

    if not rows:
        await update.message.reply_text("No news today.")
        return

    total = sum(r["cnt"] for r in rows)
    lines = [f"Today's stats (total: {total}):"]
    for r in rows:
        lines.append(f"  {r['status']}: {r['cnt']}")

    await update.message.reply_text("\n".join(lines))


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick digest of last 24h top news."""
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("Access denied.")
        return

    from storage.database import _is_postgres
    ph = "%s" if _is_postgres() else "?"

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    sql = (
        f"SELECT n.title, n.source, n.status, na.total_score "
        f"FROM news n LEFT JOIN news_analysis na ON n.id = na.news_id "
        f"WHERE n.parsed_at >= {ph} AND na.total_score IS NOT NULL "
        f"ORDER BY na.total_score DESC LIMIT 10"
    )
    rows = _db_query(sql, (since,))

    if not rows:
        await update.message.reply_text("No scored news in last 24 hours.")
        return

    lines = ["*24h Digest - Top 10:*\n"]
    for i, r in enumerate(rows, 1):
        score = r.get("total_score", 0) or 0
        src = r.get("source", "?")
        title = (r.get("title") or "?")[:100]
        lines.append(f"{i}. [{score}] {src}: {title}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Inline keyboard callbacks
# ---------------------------------------------------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle approve/reject inline button presses."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(query.message.chat_id):
        await query.edit_message_text("Access denied.")
        return

    data = query.data or ""
    if ":" not in data:
        return

    action, news_id = data.split(":", 1)

    if action not in ("approve", "reject"):
        return

    from storage.database import update_news_status

    if action == "approve":
        update_news_status(news_id, "approved")
        # Trigger background enrichment
        try:
            import threading as _th
            from scheduler import _process_single_news
            _th.Thread(
                target=_process_single_news, args=(news_id,), daemon=True
            ).start()
        except Exception as e:
            logger.warning("Background enrich after TG approve failed: %s", e)

        await query.edit_message_text(
            query.message.text + "\n\nApproved via Telegram",
        )
    elif action == "reject":
        update_news_status(news_id, "rejected")
        await query.edit_message_text(
            query.message.text + "\n\nRejected via Telegram",
        )


# ---------------------------------------------------------------------------
# Notification helpers (called from other modules)
# ---------------------------------------------------------------------------

def _send_async(coro):
    """Schedule a coroutine on the bot's event loop from a sync context."""
    if _loop is None or _app is None:
        return
    asyncio.run_coroutine_threadsafe(coro, _loop)


async def _do_send(chat_id: int, text: str, buttons: list[list[InlineKeyboardButton]] | None = None):
    """Actually send a message."""
    if _app is None:
        return
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    try:
        await _app.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning("Telegram send failed to %s: %s", chat_id, e)


def send_notification(chat_id: int, text: str, buttons: list[list] | None = None):
    """Send a message to a specific chat. Thread-safe, non-blocking."""
    _send_async(_do_send(chat_id, text, buttons))


def _broadcast(text: str, buttons: list[list] | None = None):
    """Send a message to all authorized chat IDs."""
    chat_ids = _get_authorized_chat_ids()
    for cid in chat_ids:
        send_notification(cid, text, buttons)


def notify_high_score(news_list: list[dict]):
    """Notify about news that scored above threshold.

    Each item in news_list should have: id, title, source, total_score.
    """
    threshold = getattr(config, "TELEGRAM_NOTIFY_THRESHOLD", 70)
    for news in news_list:
        score = news.get("total_score", 0) or 0
        if score < threshold:
            continue
        title = (news.get("title") or "?")[:120]
        source = news.get("source", "?")
        news_id = news.get("id", "")
        text = (
            f"*High score: {score}*\n"
            f"Source: {source}\n"
            f"{title}"
        )
        buttons = [[
            InlineKeyboardButton("Approve", callback_data=f"approve:{news_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject:{news_id}"),
        ]]
        _broadcast(text, buttons)


def notify_pipeline_done(pipeline_type: str, stats: dict):
    """Notify when a pipeline finishes.

    pipeline_type: 'auto_review', 'full_auto', 'no_llm', etc.
    stats: dict with counts like reviewed, duplicates, approved, etc.
    """
    lines = [f"*Pipeline done: {pipeline_type}*"]
    for k, v in stats.items():
        lines.append(f"  {k}: {v}")
    _broadcast("\n".join(lines))


# ---------------------------------------------------------------------------
# Bot startup
# ---------------------------------------------------------------------------

def start_bot_polling():
    """Start the Telegram bot in a background thread.

    Call this from main.py. Runs its own asyncio event loop in a daemon thread.
    """
    global _app, _loop

    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN not set, Telegram bot disabled")
        return

    def _run():
        global _app, _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)

        _app = Application.builder().token(token).build()

        _app.add_handler(CommandHandler("start", cmd_start))
        _app.add_handler(CommandHandler("top", cmd_top))
        _app.add_handler(CommandHandler("stats", cmd_stats))
        _app.add_handler(CommandHandler("digest", cmd_digest))
        _app.add_handler(CallbackQueryHandler(callback_handler))

        logger.info("Telegram bot starting polling...")
        _app.run_polling(allowed_updates=Update.ALL_TYPES)

    thread = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    thread.start()
    logger.info("Telegram bot thread started")
