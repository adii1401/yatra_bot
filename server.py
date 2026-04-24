import os
import httpx
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from telegram import Update, User as TGUser
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.request import HTTPXRequest
from sqlalchemy import select
from dotenv import load_dotenv

from bot.database.db_config import init_db, AsyncSessionLocal, Expense, User
from bot.handlers.expenses import record_expense, handle_expense_callback, check_balance
from bot.handlers.logistics import track_location, get_weather, plan_trip, where_is_everyone
from bot.handlers.itinerary import explore_nearby, show_plan, trip_gallery, set_gallery, sos_emergency, add_landmark, set_plan
from bot.handlers.vault import save_to_vault, open_vault, get_vault_file 
from bot.utils.logger import setup_logger

# Initialize environment and logger
load_dotenv()
logger = setup_logger("MasterServer")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

# 1. Initialize Telegram Bot Instance (Hardened Network & Webhook Updater)
t_request = HTTPXRequest(connection_pool_size=8, connect_timeout=15.0, read_timeout=15.0, http_version="1.1")
bot_app = Application.builder().token(TOKEN).request(t_request).updater(None).build()

# 2. LIFESPAN: Consolidates DB, Handlers, and The Monkeypatch
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Database & Log ---
    logger.info("🗄️ Initializing database...")
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

    # --- THE MONKEYPATCH: Manual Identity Injection ---
    try:
        bot_id = int(TOKEN.split(':')[0])
        # We manually build the User object so the bot doesn't call get_me()
        bot_app.bot._bot = TGUser(id=bot_id, first_name="TripOS", is_bot=True, username="yatra_os_bot")
        
        # We manually flip the flags that say "I am ready"
        bot_app.bot._initialized = True
        bot_app._initialized = True
        logger.info(f"🤖 Bot Identity Hard-Injected (ID: {bot_id})")
    except Exception as e:
        logger.error(f"❌ Monkeypatch failed: {e}")

    yield
    logger.info("🛑 Shutting down server...")

# 3. FastAPI App Setup
app = FastAPI(lifespan=lifespan, title="Trip OS Master Node")

# --- WEBHOOK LOGIC ---

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        background_tasks.add_task(process_update, data)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Webhook Entry Error: {e}")
        return {"status": "error"}

async def process_update(data: dict):
    try:
        # We parse the JSON using the mocked bot instance
        update = Update.de_json(data, bot_app.bot)
        
        # We ensure the application state is 'running' without re-initializing
        if not bot_app.running:
            bot_app.running = True 
            await bot_app.start()
            
        await bot_app.process_update(update)
    except Exception as e:
        logger.error(f"❌ Failed to process update: {e}")

# --- HEALTH & DASHBOARD ---

@app.get("/health")
async def health_check():
    return {"status": "alive"}

@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Expense, User.name)
            .join(User, Expense.payer_id == User.telegram_id)
            .where(Expense.is_verified == True)
            .order_by(Expense.id.desc())
        )
        expenses = result.all()

    total_spent = sum([row.Expense.amount for row in expenses])

    html_content = f"""
    <html>
        <head><title>Trip Dashboard</title></head>
        <body style="font-family: sans-serif; padding: 40px; background: #f0f2f5;">
            <div style="max-width: 800px; margin: auto; background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                <h1 style="text-align: center; color: #1a73e8;">🏔️ Squad Trip Ledger</h1>
                <h2 style="text-align: center;">Total: ₹{total_spent:,.2f}</h2>
                <hr>
                <table style="width: 100%; border-collapse: collapse; margin-top: 20px;">
                    <thead><tr style="background: #f8f9fa; border-bottom: 2px solid #eee;">
                        <th style="padding: 12px; text-align: left;">Payer</th>
                        <th style="padding: 12px; text-align: left;">Amount</th>
                        <th style="padding: 12px; text-align: left;">Description</th>
                    </tr></thead>
                    <tbody>
    """
    for row in expenses:
        html_content += f"<tr><td style='padding:12px; border-bottom:1px solid #eee;'><b>{row.name}</b></td><td style='padding:12px; border-bottom:1px solid #eee;'>₹{row.Expense.amount:,.2f}</td><td style='padding:12px; border-bottom:1px solid #eee;'>{row.Expense.description}</td></tr>"
    
    html_content += "</tbody></table></div></body></html>"
    return html_content