import asyncio
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from src.handlers import start_command, help_command, unknown_command, button_callback, message_handler
from src.core.redis_client import acquire_master_lock
from src.db import init_db
from src.config import TELEGRAM_BOT_TOKEN as BOT_TOKEN

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("[CONFIG] Loading environment from .env")
    logger.info("🚀 Forging the legacy daemon...")

    # Initialize database
    await init_db()

    # Acquire Redis lock
    if not acquire_master_lock():
        logger.warning("Another bot instance is active. This instance will stand down.")
        return

    # Initialize bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Start polling
    logger.info("🌀 Bot is now polling. The daemon is awake.")
    await application.run_polling()
    logger.info("🛑 Bot has shut down gracefully.")

if __name__ == "__main__":
    asyncio.run(main())
