import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict
from telegram.helpers import escape_markdown
import asyncio
import os

from . import db
from . import config

logger = logging.getLogger(__name__)

# === Utility Functions ===

async def get_user_id(update: Update) -> int | None:
    """Extracts user ID from an update."""
    if update.effective_user:
        return update.effective_user.id
    return None

def build_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    keyboard = []
    setting_order = [
        'autotrade', 'trading_mode', 'rsi_buy', 'rsi_sell', 'stop_loss',
        'trailing_activation', 'trailing_drop', 'profit_target', 'paper_balance', 'watchlist'
    ]

    for key in setting_order:
        value = settings.get(key)
        display_value = f": {value[:30]}..." if key == 'watchlist' and value and len(value) > 30 else f": {value}"

        if key == 'autotrade':
            action = 'off' if value == 'on' else 'on'
            button_text = f"Auto-Trading: {'✅ ON' if value == 'on' else '❌ OFF'}"
            callback_data = f"set:{key}:{action}"
        elif key == 'trading_mode':
            action = 'PAPER' if value == 'LIVE' else 'LIVE'
            button_text = f"Mode: {'💵 LIVE' if value == 'LIVE' else '📄 PAPER'}"
            callback_data = f"set:{key}:{action}"
        else:
            button_text = f"{key.replace('_', ' ').title()}{display_value}"
            callback_data = f"prompt:{key}"

        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    keyboard.append([InlineKeyboardButton("Done", callback_data="settings_done")])
    return InlineKeyboardMarkup(keyboard)

# === Core Command Handlers ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Start command triggered")
    user_id = await get_user_id(update)
    if not user_id: return

    logger.info(f"User {user_id} ({update.effective_user.full_name}) started the bot.")
    _, created = await db.get_or_create_user(user_id)

    welcome_message = (
        "⚔️ Welcome to the Empire, Commander. Your command center is ready."
        if created else
        "⚔️ Welcome back, Commander. Your legions await your command."
    )
    await update.message.reply_text(
        escape_markdown(f"{welcome_message}\n\nUse /help to see available commands.", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays a list of available commands."""
    logger.info("Help command triggered")
    help_text = """
*Your Imperial Command Manual*

/start - Initialize your command center.
/help - Display this command manual.
/status - View your current settings and open trades.
/myprofile - Alias for /status.
/settings - Open the interactive settings panel.
/pay - View subscription and payment information.
/diagnose_slip - Diagnose your trade slip for errors.
/addcoin <symbol> - Add a coin to your watchlist.
/removecoin <symbol> - Remove a coin from your watchlist.
/addcoins <symbol1> <symbol2> ... - Add multiple coins.
/removecoins <symbol1> <symbol2> ... - Remove multiple coins.
/backup - Download a backup of your settings.
/restore - Restore settings from a backup.
/reset - Reset your profile to defaults.
/journal - View your trading journal.
/alert - Send an admin alert.

For more details, use /settings or contact support.
"""
    logger.info("Sending help reply...")
    await update.message.reply_text(
        escape_markdown(help_text, version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Status command triggered")
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    open_trades = await db.get_open_trades_by_user(user_id)

    status_text = "*Your Imperial Command Center*\n\n"

    # --- The Corrected Treasury Section ---
    if settings.get('trading_mode') == 'PAPER':
        paper_balance = settings.get('paper_balance', 0.0)
        formatted_balance = f"${paper_balance:,.2f}"
        status_text += f"💰 *Imperial Treasury (Paper):* `{formatted_balance}`\n\n"

    # --- Strategic Settings Section ---
    status_text += "*Strategic Settings:*
"
    settings_for_display = settings.copy()
    settings_for_display.pop('paper_balance', None)
    settings_for_display.pop('watchlist', None)

    for key, value in settings_for_display.items():
        key_name = key.replace('_', ' ').title()
        value_str = str(value)
        status_text += f"- *{key_name}*: `{value_str}`\n"

    # --- Active Campaigns Section ---
    if open_trades:
        status_text += "\n*Active Campaigns (Open Trades):*\n"
        for trade in open_trades:
            symbol = trade['symbol']
            buy_price = f"${trade['buy_price']:,.4f}"
            status_text += f"- `{symbol}` @ {buy_price}\n"
    else:
        status_text += "\n*No active campaigns at this time.*\n"

    status_text += "\n_Use /settings to modify all parameters._"

    await update.message.reply_text(
        escape_markdown(status_text, version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Myprofile command triggered")
    await status_command(update, context)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Settings command triggered")
    user_id = await get_user_id(update)
    if not user_id: return

    settings = await db.get_user_effective_settings(user_id)
    keyboard = build_settings_keyboard(settings)
    await update.message.reply_text("Choose a setting to adjust, or select a toggle:", reply_markup=keyboard)

async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        if "Query is too old" in str(e):
            logger.warning(f"Handled old query for user {update.effective_user.id}. Bot may have been busy.")
            return
        else:
            raise

    user_id = await get_user_id(update)
    if not user_id: return

    parts = query.data.split(':')
    action = parts[0]

    if action == 'settings_done':
        await query.edit_message_text(
            escape_markdown("Settings saved. The empire adapts to your command.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    setting_key = parts[1]

    if action == 'set':
        new_value = parts[2]
        await db.update_user_setting(user_id, setting_key, new_value)
        logger.info(f"User {user_id} toggled setting '{setting_key}' to '{new_value}'.")
        new_settings = await db.get_user_effective_settings(user_id)
        keyboard = build_settings_keyboard(new_settings)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except BadRequest as e:
            if "Message is not modified" not in str(e): raise

    elif action == 'prompt':
        context.user_data['awaiting_setting'] = setting_key
        setting_name = setting_key.replace('_', ' ').title()
        await query.message.reply_text(
            escape_markdown(f"Please enter the new value for *{setting_name}*.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Received message from user {update.effective_user.id}: {update.message.text}")
    user_id = await get_user_id(update)
    if not user_id or 'awaiting_setting' not in context.user_data:
        return

    setting_key = context.user_data.pop('awaiting_setting')
    new_value = update.message.text

    try:
        await db.update_user_setting(user_id, setting_key, new_value)
        setting_name = setting_key.replace('_', ' ').title()
        logger.info(f"User {user_id} set '{setting_key}' to '{new_value}'.")
        await update.message.reply_text(
            escape_markdown(f"✅ *{setting_name}* has been updated.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        settings = await db.get_user_effective_settings(user_id)
        keyboard = build_settings_keyboard(settings)
        await update.message.reply_text("Settings updated. Choose another setting or select Done:", reply_markup=keyboard)
    except ValueError as e:
        await update.message.reply_text(escape_markdown(str(e), version=2))
    except Exception as e:
        logger.error(f"Failed to update setting {setting_key} for user {user_id}: {e}")
        await update.message.reply_text(
            escape_markdown("An error occurred. The Imperial Guard has been notified.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

PAYMENT_MESSAGE = '''
<b>💳 Subscription & Payment Information</b>

To unlock the full power of the empire, a subscription is required.

Please contact the administration to arrange for payment and activation.
'''

async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Pay command triggered")
    if update.effective_chat and update.effective_chat.type != 'private':
        await update.message.reply_text("For your security, please use this command in a private chat with me.")
        return
    await update.message.reply_html(PAYMENT_MESSAGE)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors and handle specific cases like Telegram Conflict."""
    
    # Handle the case where another bot instance is running.
    if isinstance(context.error, Conflict):
        logger.critical(
            "TELEGRAM CONFLICT: Another bot instance is running with the same token. "
            "This instance will now perform a hard shutdown to resolve the conflict."
        )
        # This is a hard exit. It's not graceful, but it's necessary to stop the zombie process.
        os._exit(1)

    # Suppress common, non-critical errors that are already handled.
    if isinstance(context.error, BadRequest) and (
        "Message is not modified" in str(context.error) 
        or "Query is too old" in str(context.error)
    ):
        return

    # Log all other exceptions.
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    # Optionally, notify the user about the error.
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                escape_markdown("An internal error occurred. The Imperial Guard has been notified.", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Failed to send final error message to user: {e}")

async def shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gracefully shuts down the bot."""
    logger.info("Shutdown command triggered")
    user_id = await get_user_id(update)
    if user_id != config.ADMIN_USER_ID:
        await update.message.reply_text("You are not authorized to perform this action.")
        return

    await update.message.reply_text("The empire is laying to rest... Goodbye.")
    
    # Get the shutdown_event from context and set it
    shutdown_event = context.bot_data.get('shutdown_event')
    if shutdown_event:
        shutdown_event.set()
