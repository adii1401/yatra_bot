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
    """Standard welcome command."""
    # 🚨 User-Friendly warning if used in the group
    if update.message.chat.type != 'private':
        await update.message.reply_text("👋 Hi! I'm Trip OS. Please send me a private message to see my setup guide.", parse_mode='HTML')
        return

    await update.message.reply_text(
        "🏔️ <b>Welcome to Trip OS!</b>\n\n"
        "I am your expedition assistant. I manage expenses, "
        "track locations, and keep your squad synced.\n\n"
        "Use <code>/help</code> to see the full list of commands.\n"
        "To begin, create a Telegram Group for your trip, add me to it, and make me an Admin!",
        parse_mode='HTML'
    )

async def help_command(update: Update, context):
    """Shows the help manual."""
    # 🚨 User-Friendly warning if used in the group
    if update.message.chat.type != 'private':
        await update.message.reply_text("⚠️ To keep the group chat clean, I only send the manual in private messages. Please DM me /help!", parse_mode='HTML')
        return

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
    """Generates the dashboard link and posts it in the group."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if update.message.chat.type == 'private':
        await update.message.reply_text("⚠️ Please use this command inside your trip group.")
        return

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        if user_id not in [admin.user.id for admin in admins]:
            await update.message.reply_text("⚠️ Only group admins can generate the dashboard link.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Make me an Admin so I can verify your permissions!")
        return

    # 🚨 Now posts directly to the group chat instead of DMs
    dashboard_url = f"https://yatra-bot.onrender.com/?chat_id={chat_id}"
    await update.message.reply_text(
        f"📊 <b>Your Trip Dashboard</b>\n\nHere is the admin link for this group:\n{dashboard_url}",
        parse_mode='HTML',
        disable_web_page_preview=True
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🗄️ Initializing Database...")
    await init_db()
    
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not TOKEN:
        logger.critical("❌ No TELEGRAM_BOT_TOKEN found. Bot will not start.")
    else:
        try:
            logger.info("🤖 Starting Telegram Bot Polling...")
            bot_app = Application.builder().token(TOKEN).build()
            
            # 🛑 PERSONAL CHAT COMMANDS (Filters removed so they can reply gracefully in groups)
            bot_app.add_handler(CommandHandler("start", start))
            bot_app.add_handler(CommandHandler("help", help_command))
            
            # 🌍 GROUP CHAT COMMANDS
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
            
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling(drop_pending_updates=True)
            app.state.bot_app = bot_app
            logger.info("✅ Bot is live and listening for messages.")
        except Exception as e:
            logger.error(f"❌ Bot failed to start: {e}")

    yield

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