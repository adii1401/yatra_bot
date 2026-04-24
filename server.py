import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from telegram import Update
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

# 1. Initialize Telegram Bot Instance (Webhook Mode & Hardened Network)
t_request = HTTPXRequest(connection_pool_size=8, connect_timeout=60.0, read_timeout=60.0, http_version="1.1")
bot_app = Application.builder().token(TOKEN).request(t_request).updater(None).build()

# 2. SINGLE Lifespan Function (No duplicates!)
@asynccontextmanager
async def lifespan(app: FastAPI):
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
    
    logger.info("🚀 Booting Telegram Application...")
    await bot_app.initialize()
    await bot_app.start()
    
    # 🔗 Webhook Registration
    if WEBHOOK_URL:
        clean_url = WEBHOOK_URL.rstrip('/')
        full_webhook_path = f"{clean_url}/webhook"
        logger.info(f"🔗 Locking webhook to: {full_webhook_path}")
        await bot_app.bot.set_webhook(url=full_webhook_path, drop_pending_updates=True)
    else:
        logger.critical("🚨 No WEBHOOK_URL found in .env!")
        
    yield
    
    # Clean Shutdown
    logger.info("🛑 Shutting down server...")
    await bot_app.bot.delete_webhook()
    await bot_app.stop()
    await bot_app.shutdown()

# 3. Create FastAPI App
app = FastAPI(lifespan=lifespan, title="Trip OS Master Node")

# --- ROUTES ---

@app.get("/health")
async def health_check():
    return {"status": "alive", "engine": "FastAPI + Telegram Webhook"}

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        background_tasks.add_task(process_update, data)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Webhook Error: {e}")
        return {"status": "error"}

async def process_update(data: dict):
    try:
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        logger.error(f"Failed to process update: {e}")

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
        <head>
            <title>Trip Dashboard</title>
            <style>
                body {{ font-family: 'Segoe UI', sans-serif; background: #f0f2f5; padding: 40px; }}
                .container {{ max-width: 800px; margin: auto; background: white; padding: 20px; border-radius: 12px; }}
                .header {{ background: #1a73e8; color: white; padding: 20px; border-radius: 8px; text-align: center; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #f8f9fa; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🏔️ Squad Trip Ledger</h1>
                    <h2>Total: ₹{total_spent:,.2f}</h2>
                </div>
                <table>
                    <thead><tr><th>Payer</th><th>Amount</th><th>Description</th></tr></thead>
                    <tbody>
    """
    for row in expenses:
        html_content += f"<tr><td><b>{row.name}</b></td><td>₹{row.Expense.amount:,.2f}</td><td>{row.Expense.description}</td></tr>"
        
    html_content += "</tbody></table></div></body></html>"
    return html_content

@app.on_event("startup")
async def print_routes():
    for route in app.routes:
        logger.info(f"🛣️ Route Available: {route.path}")