import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.helpers import escape_markdown

from . import db
from .commands import get_user_id, build_settings_keyboard, ONBOARDING_WATCHLIST

logger = logging.getLogger(__name__)

async def onboarding_trading_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's choice of trading mode during onboarding."""
    query = update.callback_query
    await query.answer()
    user_id = await get_user_id(update)
    if not user_id: return ConversationHandler.END

    mode = "LIVE" if query.data == "onboarding_live" else "PAPER"
    await db.update_user_setting(user_id, "trading_mode", mode)

    await query.edit_message_text(
        escape_markdown(f"You have chosen the path of *{mode}* trading.\n\n" \
                        "Now, name the assets you wish to watch. Provide a comma-separated list of symbols (e.g., BTCUSDT, ETHUSDT).", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return ONBOARDING_WATCHLIST

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
