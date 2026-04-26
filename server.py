import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)
from dotenv import load_dotenv

# 🛠️ UTILS & DB
from bot.database.db_config import init_db
from bot.handlers.expenses import (
    record_expense, handle_expense_callback,
    check_balance, set_members, export_expenses
)
from bot.handlers.logistics import track_location, get_weather, plan_trip, where_is_everyone
from bot.handlers.itinerary import (
    explore_nearby, show_plan, trip_gallery, set_gallery,
    sos_emergency, handle_sos_callback, add_landmark, set_plan,
    packing_list, handle_packing_callback, add_packing_item
)
from bot.handlers.vault import save_to_vault, open_vault, get_vault_file
from bot.utils.logger import setup_logger

# 🚨 IMPORT YOUR NEW ROUTER HERE
from web.dashboard import router as dashboard_router

load_dotenv()
logger = setup_logger("MasterServer")
limiter = Limiter(key_func=get_remote_address)

async def start(update: Update, context):
    """Standard welcome command (Personal Chat Only)."""
    await update.message.reply_text(
        "🏔️ <b>Welcome to Trip OS!</b>\n\n"
        "I am your expedition assistant. I manage expenses, "
        "track locations, and keep your squad synced.\n\n"
        "To get started, add me to your trip's Telegram group and type <code>/plan_trip</code>.",
        parse_mode='HTML'
    )

async def help_command(update: Update, context):
    """Shows the help manual (Personal Chat Only)."""
    await update.message.reply_text(
        "🏔️ <b>Trip OS Manual</b>\n\n"
        "<b>Group Commands (Use in the Trip Group):</b>\n"
        "📍 <code>/plan_trip [name]</code> - Set destination (Admins only)\n"
        "💸 <code>/paid [amount] [desc]</code> - Log an expense\n"
        "⚖️ <code>/balance</code> - Check squad settlements\n"
        "🎒 <code>/packing</code> - View packing list\n"
        "➕ <code>/add_item [name]</code> - Add a packing item\n"
        "🗺️ <code>/whereis</code> - See everyone's live location\n"
        "🌤️ <code>/weather</code> - Get destination weather\n"
        "📌 <code>/add_landmark [name]</code> - Save a location (reply to a pin)\n"
        "🔍 <code>/explore</code> - Find saved landmarks\n"
        "🚨 <code>/sos</code> - Emergency alert\n\n"
        "<b>Personal Commands (DM only):</b>\n"
        "ℹ️ <code>/help</code> - Show this manual\n"
        "🚀 <code>/start</code> - Welcome message",
        parse_mode='HTML'
    )

async def get_dashboard_link(update: Update, context):
    """Generates the dashboard link and DMs it to admins."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if update.message.chat.type == 'private':
        await update.message.reply_text("⚠️ Please use this command inside your trip group.")
        return

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        if user_id not in [admin.user.id for admin in admins]:
            await update.message.reply_text("⚠️ Only group admins can access the dashboard link.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Make me an Admin so I can verify your permissions!")
        return

    # Build and send the secure URL
    dashboard_url = f"https://yatra-bot.onrender.com/?chat_id={chat_id}"
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📊 <b>Your Trip Dashboard</b>\n\nSecure admin link:\n{dashboard_url}",
            parse_mode='HTML'
        )
        await update.message.reply_text("✅ I have sent the dashboard link securely to your DMs!")
    except Exception:
        await update.message.reply_text("⚠️ I couldn't DM you! Send me a private message first, then try again.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize Database
    logger.info("🗄️ Initializing Database...")
    await init_db()
    
    # 2. Initialize Telegram Bot
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    # FIX: Corrected the logic gate to only start if Token exists
    if not TOKEN:
        logger.critical("❌ No TELEGRAM_BOT_TOKEN found. Bot will not start.")
    else:
        try:
            logger.info("🤖 Starting Telegram Bot Polling...")
            bot_app = Application.builder().token(TOKEN).build()
            
            # 🛑 PERSONAL CHAT COMMANDS (Only work in DMs)
            bot_app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
            bot_app.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
            
            # 🌍 GROUP CHAT COMMANDS (Only work in the Trip Group)
            bot_app.add_handler(CommandHandler("plan_trip", plan_trip, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("whereis", where_is_everyone, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("weather", get_weather, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("paid", record_expense, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("balance", check_balance, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("sos", sos_emergency, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("packing", packing_list, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("add_item", add_packing_item, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("explore", explore_nearby, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("add_landmark", add_landmark, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("dashboard", get_dashboard_link, filters=filters.ChatType.GROUPS))

            # 🗄️ HYBRID/UTILITY COMMANDS
            bot_app.add_handler(CommandHandler("vault", open_vault))
            bot_app.add_handler(MessageHandler(filters.LOCATION, track_location))
            bot_app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, save_to_vault))
            
            # 🔘 BUTTON CALLBACKS
            bot_app.add_handler(CallbackQueryHandler(handle_expense_callback, pattern="^exp_"))
            bot_app.add_handler(CallbackQueryHandler(handle_packing_callback, pattern="^pack_"))
            bot_app.add_handler(CallbackQueryHandler(handle_sos_callback, pattern="^sos_"))
            bot_app.add_handler(CallbackQueryHandler(get_vault_file, pattern="^getv_"))
            
            # Initialize and Start
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling(drop_pending_updates=True)
            app.state.bot_app = bot_app
            logger.info("✅ Bot is live and listening for messages.")
        except Exception as e:
            logger.error(f"❌ Bot failed to start: {e}")

    yield

    # 3. Shutdown
    if TOKEN and hasattr(app.state, 'bot_app'):
        logger.info("🛑 Shutting down Bot...")
        await app.state.bot_app.updater.stop()
        await app.state.bot_app.stop()
        await app.state.bot_app.shutdown()

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.get("/health")
async def health():
    return {"status": "healthy", "bot_running": hasattr(app.state, 'bot_app')}

# 🚨 PLUG IN THE DASHBOARD ROUTER HERE
app.include_router(dashboard_router)