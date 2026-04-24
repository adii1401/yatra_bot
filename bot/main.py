import os
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

from bot.database.db_config import init_db
from bot.handlers.expenses import record_expense, handle_expense_callback, check_balance 
from bot.utils.logger import setup_logger 

logger = setup_logger("MainApp")
load_dotenv()

async def main():
    logger.info("🗄️ Initializing database...")
    await init_db()
    logger.info("✅ Database ready!")
    
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.critical("🚨 TELEGRAM_BOT_TOKEN is missing from .env file!")
        return

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot is Active! 🙏")))
    app.add_handler(CommandHandler("paid", record_expense)) 
    app.add_handler(CommandHandler("balance", check_balance)) 
    app.add_handler(CallbackQueryHandler(handle_expense_callback))
    
    logger.info("✅ All Handlers registered successfully.")
    logger.info("🚀 Starting polling sequence...")

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped manually by user.")
    except Exception as e:
        logger.critical("🚨 CRITICAL CRASH", exc_info=True)
