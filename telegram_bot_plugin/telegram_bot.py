import asyncio
import datetime as dt
import functools
import html
import time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import core
import db
from logger import get_logger

# Get logger for this module
logger = get_logger(__name__)

MAX_CAPTION_LENGTH = 1024
MAX_MESSAGE_LENGTH = 4096
OUTBOX_BATCH = 10
OUTBOX_MAX_ATTEMPTS = 5
TELEGRAM_TIMEOUT = 40

WELCOME_TEXT = (
    "<b>Vinted Notifications</b>\n\n"
    "<b>Queries</b>\n"
    "/add_query [name=]url - add a search\n"
    "/queries - list and manage searches\n"
    "/remove_query number|all - remove a search\n"
    "/test url - try a search without saving it\n\n"
    "<b>Filters</b>\n"
    "/banwords - list the excluded words\n"
    "/add_banword word - exclude a word\n"
    "/remove_banword word - stop excluding a word\n"
    "/allowlist - show the country allowlist\n"
    "/add_country XX - allow a country\n"
    "/remove_country XX - remove a country\n"
    "/clear_allowlist - allow every country\n\n"
    "<b>Notifications</b>\n"
    "/pause - stop sending notifications\n"
    "/resume - resume notifications\n"
    "/quiet HH:MM-HH:MM - silence window (/quiet off to disable)\n\n"
    "<b>Diagnostics</b>\n"
    "/status - session and queue status\n"
    "/stats - notification statistics\n"
    "/refresh_session - renew the Vinted browser session\n"
    "/hello - check that the bot is alive"
)


def restricted(func):
    """Only answer the configured chat."""

    @functools.wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            logger.warning(
                f"Ignored command from unauthorized chat {update.effective_chat_id}"
            )
            return None
        return await func(self, update, context)

    return wrapper


class LeRobot:
    def __init__(self):
        try:
            self.app = (
                ApplicationBuilder().token(db.get_parameter("telegram_token")).build()
            )
            self.bot = self.app.bot
            self.chat_id = str(db.get_parameter("telegram_chat_id") or "")
            self._sending = False
            self._last_expire = 0.0

            self._register_handlers()

            job_queue = self.app.job_queue
            # Set the commands
            job_queue.run_once(self.set_commands, when=1)
            # Every day we check for a new version
            job_queue.run_repeating(self.check_version, interval=86400, first=1)
            # Every few seconds we flush the pending notifications
            job_queue.run_repeating(self.process_outbox, interval=2, first=1)

            self.app.run_polling()
        except Exception as e:
            logger.error(f"Error initializing bot: {str(e)}", exc_info=True)

    def _register_handlers(self):
        commands = [
            ("start", self.start),
            ("help", self.help),
            ("hello", self.hello),
            ("add_query", self.add_query),
            ("remove_query", self.remove_query),
            ("queries", self.queries),
            ("clear_allowlist", self.clear_allowlist),
            ("add_country", self.add_country),
            ("remove_country", self.remove_country),
            ("allowlist", self.allowlist),
            ("banwords", self.banwords),
            ("add_banword", self.add_banword),
            ("remove_banword", self.remove_banword),
            ("pause", self.pause),
            ("resume", self.resume),
            ("quiet", self.quiet),
            ("status", self.status),
            ("stats", self.stats),
            ("test", self.test),
            ("refresh_session", self.refresh_session),
        ]
        for command, callback in commands:
            self.app.add_handler(CommandHandler(command, callback))

        self.app.add_handler(CallbackQueryHandler(self.query_callback, pattern=r"^q:"))

    def _is_authorized(self, update: Update) -> bool:
        if not self.chat_id:
            return True
        chat = update.effective_chat
        return chat is not None and str(chat.id) == self.chat_id

    ### GENERIC HELPERS ###

    async def reply(self, update: Update, text: str):
        """Reply to a command, tolerating an unreachable chat."""
        try:
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error sending reply: {str(e)}", exc_info=True)

    async def send_message(
        self, content, url=None, text=None, buy_url=None, buy_text=None
    ):
        """Send a plain message with optional URL buttons."""
        keyboard = []
        if url:
            keyboard.append([InlineKeyboardButton(text or "Open Vinted", url=url)])
        if buy_url and buy_text:
            keyboard.append([InlineKeyboardButton(buy_text, url=buy_url)])
        markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        for part in core.split_message(content, MAX_MESSAGE_LENGTH):
            await self.bot.send_message(
                self.chat_id,
                part,
                parse_mode="HTML",
                read_timeout=TELEGRAM_TIMEOUT,
                write_timeout=TELEGRAM_TIMEOUT,
                reply_markup=markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

    ### LIFECYCLE ###

    @restricted
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.reply(update, WELCOME_TEXT)

    @restricted
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.reply(update, WELCOME_TEXT)

    @restricted
    async def hello(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ver = db.get_parameter("version")
        await self.reply(
            update,
            f"Hello {update.effective_user.first_name}! "
            f"Vinted-Notifications is running under version {ver}.\n",
        )

    ### QUERIES ###

    @restricted
    async def add_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = context.args
        if not query:
            await self.reply(update, "No query provided.")
            return

        # Split the message into name=query if it contains an equal sign before the url
        if "=http" in query[0]:
            name, url = query[0].split("=", 1)
        else:
            name = None
            url = query[0]

        if "vinted" not in url:
            await self.reply(
                update, "The query must be a Vinted URL (https://www.vinted.xx/...)."
            )
            return

        message, is_new_query = core.process_query(url, name)
        if is_new_query:
            query_list = core.get_formatted_query_list()
            await self.reply(update, f"{message} \nCurrent queries: \n{query_list}")
        else:
            await self.reply(update, message)

    @restricted
    async def remove_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        number = context.args
        if not number:
            await self.reply(update, "No number provided.")
            return

        target = number[0]
        if target != "all":
            if not target.isdigit():
                await self.reply(update, "Invalid number.")
                return
            query_id = db.get_query_id_by_rowid(target)
            if query_id is None:
                await self.reply(update, "Invalid number.")
                return
            target = str(query_id)

        message, success = core.process_remove_query(target)
        if success and target != "all":
            query_list = core.get_formatted_query_list()
            await self.reply(update, f"{message} \nCurrent queries: \n{query_list}")
        else:
            await self.reply(update, message)

    @restricted
    async def queries(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        rows = db.get_queries_with_status()
        if not rows:
            await self.reply(update, "No queries configured.")
            return

        keyboard = []
        lines = []
        for index, (query_id, url, name, enabled) in enumerate(rows, start=1):
            label = name or url
            state = "▶️" if enabled else "⏸️"
            lines.append(f"{index}. {state} {label}")
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{state} {label}"[:60], callback_data=f"q:t:{query_id}"
                    ),
                    InlineKeyboardButton("🗑", callback_data=f"q:d:{query_id}"),
                ]
            )

        await update.message.reply_text(
            "<b>Current queries</b>\n" + core.split_message("\n".join(lines))[0],
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    @restricted
    async def query_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query_data = update.callback_query
        if not self._is_authorized(update):
            await query_data.answer("Not allowed", show_alert=True)
            return
        await query_data.answer()

        try:
            _, action, raw_id = query_data.data.split(":")
            query_id = int(raw_id)
        except (ValueError, AttributeError):
            await query_data.edit_message_text("Invalid query reference.")
            return

        row = db.get_query(query_id)
        if row is None:
            await query_data.edit_message_text("Query not found.")
            return

        if action == "t":
            enabled = not bool(row[4])
            db.set_query_enabled(query_id, enabled)
            state = "enabled" if enabled else "paused"
            await query_data.edit_message_text(f"Query {query_id} {state}.")
        elif action == "d":
            db.remove_query_from_db(query_id)
            await query_data.edit_message_text(f"Query {query_id} removed.")
        else:
            await query_data.edit_message_text("Unknown action.")

    ### ALLOWLIST ###

    @restricted
    async def clear_allowlist(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        db.clear_allowlist()
        await self.reply(update, "Allowlist cleared. All countries are allowed.")

    @restricted
    async def add_country(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        country = context.args
        if not country:
            await self.reply(update, "No country provided")
            return

        message, country_list = core.process_add_country(" ".join(country))
        await self.reply(update, f"{message} Current allowlist: {country_list}")

    @restricted
    async def remove_country(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        country = context.args
        if not country:
            await self.reply(update, "No country provided")
            return

        message, country_list = core.process_remove_country(" ".join(country))
        await self.reply(update, f"{message} Current allowlist: {country_list}")

    @restricted
    async def allowlist(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        allowlist = db.get_allowlist()
        if allowlist == 0:
            await self.reply(update, "No allowlist set. All countries are allowed.")
        else:
            await self.reply(update, f"Current allowlist: {allowlist}")

    ### BANWORDS ###

    @restricted
    async def banwords(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        banwords = core.parse_banwords(db.get_parameter("banwords"))
        if not banwords:
            await self.reply(update, "No banwords configured.")
        else:
            await self.reply(update, "Banwords: " + ", ".join(banwords))

    @restricted
    async def add_banword(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        word = " ".join(context.args).strip()
        if not word:
            await self.reply(update, "No word provided.")
            return
        updated = core.add_banword(db.get_parameter("banwords"), word)
        db.set_parameter("banwords", updated)
        await self.reply(update, f"Banword added. Banwords: {updated}")

    @restricted
    async def remove_banword(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        word = " ".join(context.args).strip()
        if not word:
            await self.reply(update, "No word provided.")
            return
        updated = core.remove_banword(db.get_parameter("banwords"), word)
        db.set_parameter("banwords", updated)
        await self.reply(update, f"Banword removed. Banwords: {updated}")

    ### NOTIFICATIONS ###

    @restricted
    async def pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        db.set_parameter("notifications_paused", "True")
        await self.reply(
            update,
            "Notifications paused. New items stay queued and will be sent "
            "when you use /resume.",
        )

    @restricted
    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        db.set_parameter("notifications_paused", "False")
        await self.reply(update, "Notifications resumed.")

    @restricted
    async def quiet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        spec = " ".join(context.args).strip()
        if not spec:
            current = db.get_parameter("quiet_hours") or "off"
            await self.reply(
                update, f"Quiet hours: {current}\nUse /quiet 23:00-07:00 or /quiet off"
            )
            return
        if spec.lower() == "off":
            db.set_parameter("quiet_hours", "")
            await self.reply(update, "Quiet hours disabled.")
            return
        if not core.is_valid_quiet_spec(spec):
            await self.reply(update, "Invalid format. Use /quiet 23:00-07:00")
            return
        db.set_parameter("quiet_hours", spec)
        await self.reply(update, f"Quiet hours set to {spec}.")

    ### DIAGNOSTICS ###

    @restricted
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        browser_enabled = db.get_parameter("browser_enabled") == "True"
        browser_status = db.get_parameter("browser_status") or "unknown"
        try:
            state_ts = int(db.get_parameter("browser_state_ts") or 0)
        except ValueError:
            state_ts = 0
        state_age = (
            f"{int((time.time() - state_ts) // 60)} min ago" if state_ts else "never"
        )

        last_item = db.get_last_item_timestamp()
        last_item_text = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(last_item))
            if last_item
            else "never"
        )

        lines = [
            "<b>Status</b>",
            f"Browser (Patchright): {'on' if browser_enabled else 'off'} "
            f"- {browser_status}",
            f"Session: {state_age}",
            f"Last item found: {last_item_text}",
            f"Pending notifications: {db.get_outbox_count()}",
            f"Paused: {db.get_parameter('notifications_paused') or 'False'}",
            f"Quiet hours: {db.get_parameter('quiet_hours') or 'off'}",
            f"Items per query: {db.get_parameter('items_per_query')}",
            f"Refresh delay: {db.get_parameter('query_refresh_delay')}s",
        ]
        error = db.get_parameter("browser_last_error")
        if error:
            lines.append(f"Last browser error: {error}")
        await self.reply(update, "\n".join(lines))

    @restricted
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        lines = [
            "<b>Statistics</b>",
            f"Queries: {db.get_total_queries_count()}",
            f"Items stored: {db.get_total_items_count()}",
            f"Items in the last 24h: {db.get_items_count_since(int(time.time()) - 86400)}",
            f"Average items per day: {db.get_items_per_day()}",
            f"Pending notifications: {db.get_outbox_count()}",
        ]
        per_query = db.get_items_count_by_query()[:10]
        if per_query:
            lines.append("")
            lines.append("<b>Items per query</b>")
            for name, count in per_query:
                label = str(name or "(unnamed)")
                if len(label) > 60:
                    label = label[:57] + "..."
                lines.append(f"{count} - {html.escape(label)}")
        await self.reply(update, "\n".join(lines))

    @restricted
    async def test(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args
        if not args:
            await self.reply(update, "Usage: /test <vinted search url>")
            return
        url = args[0]
        if "vinted" not in url:
            await self.reply(update, "The query must be a Vinted URL.")
            return

        await self.reply(update, "Searching, please wait...")
        try:
            from pyVintedVN import Vinted

            items = await asyncio.to_thread(
                lambda: Vinted().items.search(url, nbr_items=5)
            )
        except Exception as e:
            logger.error(f"Error testing query: {str(e)}", exc_info=True)
            await self.reply(update, f"Search failed: {html.escape(str(e))}")
            return

        if not items:
            await self.reply(update, "No items found for this query.")
            return

        lines = [f"<b>{len(items)} items found</b>"]
        for item in items:
            lines.append(
                f"- {html.escape(item.title)} "
                f"({html.escape(core.format_price(item.price, item.currency))})"
            )
        await self.reply(update, "\n".join(lines))

    @restricted
    async def refresh_session(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        db.set_parameter("browser_refresh_requested", "True")
        db.set_parameter("browser_retry_after", "0")
        await self.reply(
            update,
            "Session refresh requested. It will be renewed at the next scraping "
            "cycle (use /status to check the result).",
        )

    ### DELIVERY ###

    async def process_outbox(self, context: ContextTypes.DEFAULT_TYPE):
        """Send the pending notifications, one batch at a time."""
        if self._sending:
            return
        if db.get_parameter("notifications_paused") == "True":
            return
        if core.is_quiet_time(db.get_parameter("quiet_hours")):
            return

        # Expiring/pruning are write queries: do them once every five minutes
        if time.time() - self._last_expire > 300:
            db.expire_outbox()
            db.cleanup_outbox()
            self._last_expire = time.time()

        self._sending = True
        try:
            await self._flush_outbox()
        finally:
            self._sending = False

    @staticmethod
    def _retry_after_seconds(error):
        """Return the RetryAfter delay in seconds (PTB exposes a timedelta)."""
        retry_after = getattr(error, "retry_after", 1)
        if isinstance(retry_after, dt.timedelta):
            retry_after = retry_after.total_seconds()
        try:
            return max(float(retry_after), 1.0)
        except (TypeError, ValueError):
            return 1.0

    async def _flush_outbox(self):
        for row in db.get_pending_outbox(limit=OUTBOX_BATCH):
            if row["attempts"] >= OUTBOX_MAX_ATTEMPTS:
                logger.error(
                    f"Dropping notification {row['id']} after {row['attempts']} attempts"
                )
                db.mark_outbox_sent(row["id"])
                continue

            try:
                await self.send_notification(row)
            except RetryAfter as e:
                delay = self._retry_after_seconds(e)
                logger.error(f"Flood control exceeded. Retrying in {delay:.0f}s")
                await asyncio.sleep(delay + 2)
                return
            except Exception as e:
                logger.error(f"Error sending notification: {str(e)}", exc_info=True)
                db.mark_outbox_failed(row["id"], str(e))
                return

            db.mark_outbox_sent(row["id"])
            await asyncio.sleep(1)

    async def send_notification(self, row):
        """Send a single outbox row: photo with caption when possible."""
        keyboard = []
        if row["url"]:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        row["button_text"] or "Open Vinted", url=row["url"]
                    )
                ]
            )
        if row["buy_url"] and row["buy_text"]:
            keyboard.append([InlineKeyboardButton(row["buy_text"], url=row["buy_url"])])
        markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        if row["photo_url"] and row["caption"]:
            try:
                await self.bot.send_photo(
                    self.chat_id,
                    photo=row["photo_url"],
                    caption=core.truncate_html(row["caption"], MAX_CAPTION_LENGTH),
                    parse_mode="HTML",
                    read_timeout=TELEGRAM_TIMEOUT,
                    write_timeout=TELEGRAM_TIMEOUT,
                    reply_markup=markup,
                )
                return
            except TelegramError as e:
                # Fall back to a text message (e.g. the photo URL expired)
                logger.warning(f"Could not send the photo ({e}), sending text")

        for part in core.split_message(row["content"], MAX_MESSAGE_LENGTH):
            await self.bot.send_message(
                self.chat_id,
                part,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                read_timeout=TELEGRAM_TIMEOUT,
                write_timeout=TELEGRAM_TIMEOUT,
                reply_markup=markup,
            )

    async def check_version(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            # get latest version from the repository
            should_update, VER, latest_version, url = core.check_version()

            if not should_update:
                await self.send_message(
                    f"Version {latest_version} is now available. Please update the bot.",
                    url,
                    "Open Github",
                )
        except Exception as e:
            logger.error(f"Error checking for new version: {str(e)}", exc_info=True)

    async def set_commands(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            await self.bot.set_my_commands(
                [
                    ("start", "Show the help"),
                    ("help", "Show the help"),
                    ("hello", "Verify if bot is running"),
                    ("add_query", "Add a search to monitor"),
                    ("queries", "List and manage searches"),
                    ("remove_query", "Remove a search"),
                    ("banwords", "List the excluded words"),
                    ("add_banword", "Exclude a word"),
                    ("remove_banword", "Stop excluding a word"),
                    ("pause", "Pause notifications"),
                    ("resume", "Resume notifications"),
                    ("quiet", "Set a quiet hours window"),
                    ("allowlist", "List the country allowlist"),
                    ("add_country", "Add a country to the allowlist"),
                    ("remove_country", "Remove a country from the allowlist"),
                    ("clear_allowlist", "Clear the allowlist"),
                    ("status", "Session and queue status"),
                    ("stats", "Notification statistics"),
                    ("test", "Test a search without saving it"),
                    ("refresh_session", "Renew the Vinted browser session"),
                ]
            )
            logger.info("Bot commands set successfully")
        except Exception as e:
            logger.error(f"Error setting bot commands: {str(e)}", exc_info=True)
