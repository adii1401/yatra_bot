import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select
from dotenv import load_dotenv

# Internal Project Imports
from bot.database.db_config import init_db, AsyncSessionLocal, Expense, User
from bot.handlers.expenses import record_expense, handle_expense_callback, check_balance
from bot.handlers.logistics import track_location, get_weather, plan_trip, where_is_everyone
from bot.handlers.itinerary import (
    explore_nearby, show_plan, trip_gallery, set_gallery, 
    sos_emergency, add_landmark, set_plan
)
from bot.handlers.vault import save_to_vault, open_vault, get_vault_file 
from bot.utils.logger import setup_logger

load_dotenv()
logger = setup_logger("MasterServer")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

# 1. Initialize Telegram Bot Instance
bot_app = Application.builder().token(TOKEN).updater(None).build()

# 2. Server Lifespan (Startup & Shutdown)
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🗄️ Connecting to Supabase...")
    await init_db()
    
    # --- Register Handlers ---
    bot_app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Trip OS Active! 🏔️🙏")))
    bot_app.add_handler(CommandHandler("paid", record_expense))
    bot_app.add_handler(CommandHandler("balance", check_balance))
    bot_app.add_handler(CallbackQueryHandler(handle_expense_callback))
    bot_app.add_handler(CommandHandler("plan_trip", plan_trip)) 
    bot_app.add_handler(CommandHandler("weather", get_weather)) 
    bot_app.add_handler(CommandHandler("whereis", where_is_everyone)) 
    bot_app.add_handler(CommandHandler("sos", sos_emergency))
    bot_app.add_handler(MessageHandler(filters.LOCATION, track_location)) 
    bot_app.add_handler(CommandHandler("explore", explore_nearby))
    bot_app.add_handler(CommandHandler("plan", show_plan))
    bot_app.add_handler(CommandHandler("gallery", trip_gallery)) 
    bot_app.add_handler(CommandHandler("set_gallery", set_gallery))
    bot_app.add_handler(CommandHandler("add_landmark", add_landmark))
    bot_app.add_handler(CommandHandler("set_plan", set_plan))
    bot_app.add_handler(CommandHandler("vault", open_vault)) 
    bot_app.add_handler(CommandHandler("get", get_vault_file)) 
    bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_to_vault)) 

    # 3. Official Boot & Webhook Sync
    await bot_app.initialize()
    await bot_app.start()
    
    if WEBHOOK_URL:
        webhook_path = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        await bot_app.bot.set_webhook(url=webhook_path, drop_pending_updates=True)
        logger.info(f"✅ Webhook successfully locked to: {webhook_path}")
    
    yield
    # Graceful Shutdown
    await bot_app.stop()
    await bot_app.shutdown()

# 4. FastAPI Setup
app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    background_tasks.add_task(bot_app.process_update, update)
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return "<html><body style='font-family:sans-serif; text-align:center;'><h1>🏔️ Trip OS is Online</h1><p>Master Node is listening for updates.</p></body></html>"