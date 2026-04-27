import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
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
from bot.handlers.scheduler import start_scheduler
from bot.utils.logger import setup_logger
from bot.utils.sentry import init_sentry

# 🚨 IMPORT ROUTERS & TOKENS
from web.dashboard import router as dashboard_router
from web.dashboard import ACTIVE_TOKENS

load_dotenv()
logger = setup_logger("MasterServer")
limiter = Limiter(key_func=get_remote_address)


async def start(update: Update, context):
    """Standard welcome command."""
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
    """Generates a secure UUID dashboard link and sends it PRIVATELY to the admin."""
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

    if len(ACTIVE_TOKENS) > 50:
        ACTIVE_TOKENS.pop(next(iter(ACTIVE_TOKENS)))

    sec_token = str(uuid.uuid4())
    ACTIVE_TOKENS[sec_token] = chat_id

    dashboard_url = f"https://yatra-bot.onrender.com/?token={sec_token}"

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📊 <b>Your Trip Dashboard</b>\n\n📍 Group: {update.message.chat.title}\n\n🔐 <b>Secure Admin Link:</b>\n{dashboard_url}\n\n<i>Note: For security, this link will expire if the server restarts.</i>",
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        await update.message.reply_text("✅ Secure dashboard link sent to your private messages.")
    except Exception as e:
        logger.error(f"Failed to DM dashboard link: {e}")
        await update.message.reply_text("⚠️ I couldn't DM you. Please send me a private message first (/start) so I can send you the secure link!")


async def global_error_handler(update: object, context):
    """Log the error and send a polite message to the user."""
    error_msg = str(context.error)

    if "Query is too old" in error_msg or "query id is invalid" in error_msg:
        logger.warning(f"Callback expired: {error_msg}")
        return

    logger.error(f"Exception while handling an update: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ <b>System Glitch!</b>\nMy circuits got a little tangled. The developers have been notified!",
            parse_mode='HTML'
        )
async def unknown_command(update: Update, context):
    """Catches unrecognized commands and guides the user."""
    if update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ <b>Unrecognized command!</b>\nType /help to see the expedition manual.", 
            parse_mode='HTML'
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🗄️ Initializing Database...")
    await init_db()

    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://yatra-bot.onrender.com/webhook

    if not TOKEN:
        logger.critical("❌ No TELEGRAM_BOT_TOKEN found. Bot will not start.")
    elif not WEBHOOK_URL:
        logger.critical("❌ No WEBHOOK_URL found. Bot will not start.")
    else:
        try:
            logger.info("🤖 Setting up Telegram Bot via Webhook...")
            bot_app = Application.builder().token(TOKEN).build()

            # 🛑 PERSONAL CHAT COMMANDS
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
            bot_app.add_handler(CommandHandler("set_members", set_members, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("dashboard", get_dashboard_link, filters=filters.ChatType.GROUPS))
            bot_app.add_handler(CommandHandler("export", export_expenses, filters=filters.ChatType.GROUPS))

            # 🗄️ HYBRID/UTILITY COMMANDS
            bot_app.add_handler(CommandHandler("vault", open_vault))
            bot_app.add_handler(MessageHandler(filters.LOCATION, track_location))
            bot_app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, save_to_vault))

            # 🔘 BUTTON CALLBACKS
            bot_app.add_handler(CallbackQueryHandler(handle_expense_callback, pattern="^exp_"))
            bot_app.add_handler(CallbackQueryHandler(handle_packing_callback, pattern="^pack_"))
            bot_app.add_handler(CallbackQueryHandler(handle_sos_callback, pattern="^sos_"))
            bot_app.add_handler(CallbackQueryHandler(get_vault_file, pattern="^getv_"))
            bot_app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
            bot_app.add_error_handler(global_error_handler)

            await bot_app.initialize()
            await bot_app.start()
            start_scheduler(bot_app)

            # ✅ WEBHOOK MODE — no polling conflict on redeploy
            await bot_app.bot.set_webhook(
                url=WEBHOOK_URL,
                drop_pending_updates=True
            )
            app.state.bot_app = bot_app
            logger.info(f"✅ Webhook set to {WEBHOOK_URL}")

        except Exception as e:
            logger.error(f"❌ Bot failed to start: {e}")

    yield

    # 🛑 CLEAN SHUTDOWN
    if TOKEN and hasattr(app.state, 'bot_app'):
        logger.info("🛑 Shutting down Bot...")
        await app.state.bot_app.stop()
        await app.state.bot_app.shutdown()


init_sentry()
app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.get("/health")
async def health():
    bot_running = hasattr(app.state, 'bot_app')

    if bot_running:
        WEBHOOK_URL = os.getenv("WEBHOOK_URL")
        try:
            webhook_info = await app.state.bot_app.bot.get_webhook_info()
            if webhook_info.url != WEBHOOK_URL:
                await app.state.bot_app.bot.set_webhook(
                    url=WEBHOOK_URL,
                    drop_pending_updates=False
                )
                logger.info("♻️ Webhook auto-healed")
        except Exception as e:
            logger.warning(f"Webhook heal check failed: {e}")

    return {"status": "healthy", "bot_running": bot_running}


@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    if not hasattr(app.state, 'bot_app'):
        logger.error("Webhook hit but bot_app not initialized!")
        return {"ok": False, "error": "Bot not running"}
    
    data = await request.json()
    update = Update.de_json(data, app.state.bot_app.bot)
    
    # ✅ FIX: Process the bot command in the background
    background_tasks.add_task(app.state.bot_app.process_update, update)
    
    # ✅ FIX: Instantly tell Telegram "I got it!" so it never sends duplicates
    return {"ok": True}


# 🚨 PLUG IN THE DASHBOARD ROUTER HERE
app.include_router(dashboard_router)