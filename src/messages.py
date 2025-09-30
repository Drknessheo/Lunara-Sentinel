import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown

from . import db
from .commands import get_user_id, build_settings_keyboard

logger = logging.getLogger(__name__)

async def onboarding_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's initial watchlist during onboarding."""
    user_id = await get_user_id(update)
    if not user_id: return ConversationHandler.END

    watchlist = update.message.text
    await db.update_user_setting(user_id, "watchlist", watchlist)

    await update.message.reply_text(
        escape_markdown("Your command center is now operational. Your settings have been saved.\n\n" \
                        "Use /status to see your current configuration, and /help to see all available commands.", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return ConversationHandler.END

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
            escape_markdown("An internal error occurred. The Imperial Guard has been notified.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
