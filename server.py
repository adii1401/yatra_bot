import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from dotenv import load_dotenv

# 🛠️ SCHEDULER
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from bot.database.db_config import init_db, AsyncSessionLocal, Expense, User, TripGroup, TripPlan
from bot.handlers.expenses import (
    record_expense, handle_expense_callback,
    check_balance, set_members, export_expenses
)
from bot.handlers.logistics import track_location, get_weather, plan_trip, where_is_everyone
from bot.handlers.itinerary import (
    explore_nearby, show_plan, trip_gallery, set_gallery,
    sos_emergency, handle_sos_callback, add_landmark, set_plan,
    packing_list, handle_packing_callback
)
from bot.handlers.vault import save_to_vault, open_vault, get_vault_file
from bot.utils.logger import setup_logger
from bot.utils.sentry import init_sentry

load_dotenv()
logger = setup_logger("MasterServer")
limiter = Limiter(key_func=get_remote_address)

# Initialize Sentry
init_sentry()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Production startup sequence."""
    logger.info("🗄️ Initializing database connection...")
    await init_db()
    
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    bot_app = Application.builder().token(TOKEN).build()

    # --- HANDLER REGISTRATION ---
    # Expenses
    bot_app.add_handler(CommandHandler("paid", record_expense))
    bot_app.add_handler(CommandHandler("balance", check_balance))
    bot_app.add_handler(CommandHandler("set_members", set_members))
    bot_app.add_handler(CommandHandler("export", export_expenses))

    # Logistics
    bot_app.add_handler(CommandHandler("plan_trip", plan_trip))
    bot_app.add_handler(CommandHandler("weather", get_weather))
    bot_app.add_handler(CommandHandler("whereis", where_is_everyone))
    bot_app.add_handler(MessageHandler(filters.LOCATION, track_location))

    # Itinerary & SOS
    bot_app.add_handler(CommandHandler("explore", explore_nearby))
    bot_app.add_handler(CommandHandler("plan", show_plan))
    bot_app.add_handler(CommandHandler("set_plan", set_plan))
    bot_app.add_handler(CommandHandler("add_landmark", add_landmark))
    bot_app.add_handler(CommandHandler("sos", sos_emergency))
    bot_app.add_handler(CommandHandler("packing", packing_list))
    bot_app.add_handler(CommandHandler("gallery", trip_gallery))
    bot_app.add_handler(CommandHandler("set_gallery", set_gallery))

    # Vault
    bot_app.add_handler(MessageHandler(filters.Caption(["#vault", "#doc"]), save_to_vault))
    bot_app.add_handler(CommandHandler("vault", open_vault))

    # Callbacks
    bot_app.add_handler(CallbackQueryHandler(handle_expense_callback, pattern="^(appv_|rejt_)"))
    bot_app.add_handler(CallbackQueryHandler(handle_sos_callback, pattern="^sos_"))
    bot_app.add_handler(CallbackQueryHandler(handle_packing_callback, pattern="^pack_"))
    bot_app.add_handler(CallbackQueryHandler(get_vault_file, pattern="^getv_"))

    # Start Bot
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    app.state.bot_app = bot_app

    # Scheduler: Daily 7 AM IST Briefing
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_daily_briefing,
        CronTrigger(hour=7, minute=0, timezone=ZoneInfo("Asia/Kolkata")),
        args=[bot_app]
    )
    scheduler.start()
    logger.info("✅ Daily briefing scheduled for 07:00 IST.")

    yield
    # Shutdown
    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

async def send_daily_briefing(bot_app: Application):
    """Broadcast daily weather and plan to all active groups."""
    logger.info("🌅 Generating daily briefings...")
    # Add logic to iterate through active TripGroups and send summaries

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "yatra-bot"}

@app.get("/", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def dashboard(request: Request, chat_id: int = Query(None)):
    """Professional Multi-tenant Expense Dashboard."""
    try:
        async with AsyncSessionLocal() as session:
            # Multi-tenant logic: Resolve chat_id
            if not chat_id:
                stmt = select(TripGroup).order_by(TripGroup.created_at.desc()).limit(1)
                latest_group = (await session.execute(stmt)).scalar()
                chat_id = latest_group.chat_id if latest_group else 0

            group = await session.get(TripGroup, chat_id)
            trip_name = group.trip_name if group else "Kedarnath Expedition"
            group_size = group.member_count if (group and group.member_count) else 1

            # Fetch Verified Expenses
            expense_stmt = (
                select(Expense, User.name)
                .join(User, Expense.payer_id == User.telegram_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
                .order_by(Expense.id.desc())
            )
            expenses = (await session.execute(expense_stmt)).all()

        total = sum([exp.Expense.amount for exp in expenses])
        avg = total / len(expenses) if expenses else 0
        share = total / group_size if group_size > 0 else 0

        # Generate Timeline HTML
        timeline_html = ""
        for row in expenses:
            exp = row.Expense
            dt = exp.created_at.strftime("%d %b · %I:%M %p")
            timeline_html += f"""
            <div class='timeline-item'>
                <div class='time'>{dt}</div>
                <div class='detail'>
                    <span class='who'>{row.name}</span> paid <span class='amt'>₹{exp.amount:,.0f}</span>
                    <div class='note'>{exp.description}</div>
                </div>
            </div>"""

        # Generate Settlement Grid
        user_sums = {}
        for row in expenses:
            user_sums[row.name] = user_sums.get(row.name, 0) + row.Expense.amount
        
        settlement_html = ""
        for name, paid in user_sums.items():
            diff = paid - share
            status_class = "plus" if diff >= 0 else "minus"
            settlement_html += f"""
            <div class='member-card'>
                <div class='m-name'>{name}</div>
                <div class='m-paid'>Paid: ₹{paid:,.0f}</div>
                <div class='m-status {status_class}'>{'Get back' if diff >= 0 else 'Owes'}: ₹{abs(diff):,.0f}</div>
            </div>"""

        return f"""
        <html>
            <head>
                <title>TRIP OS · {trip_name}</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    :root {{ --bg: #0f172a; --card: #1e293b; --accent: #38bdf8; --text: #f1f5f9; --green: #22c55e; --red: #ef4444; }}
                    body {{ font-family: -apple-system, system-ui; background: var(--bg); color: var(--text); margin: 0; padding-bottom: 50px; }}
                    .header {{ background: var(--card); padding: 30px 20px; border-bottom: 1px solid #334155; text-align: center; }}
                    .stats {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-top: 20px; }}
                    .stat-val {{ font-size: 1.5rem; font-weight: 800; color: var(--accent); }}
                    .stat-label {{ font-size: 0.7rem; opacity: 0.6; text-transform: uppercase; }}
                    .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
                    .section-title {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; margin: 30px 0 15px; opacity: 0.5; }}
                    .member-card {{ background: var(--card); padding: 15px; border-radius: 12px; border-left: 4px solid #334155; margin-bottom: 10px; }}
                    .plus {{ color: var(--green); }} .minus {{ color: var(--red); }}
                    .timeline-item {{ border-left: 2px solid #334155; padding-left: 20px; margin-bottom: 25px; position: relative; }}
                    .timeline-item::after {{ content: ''; width: 10px; height: 10px; background: var(--accent); border-radius: 50%; position: absolute; left: -6px; top: 5px; }}
                    .time {{ font-size: 0.75rem; opacity: 0.5; }}
                    .amt {{ font-weight: bold; color: var(--accent); }}
                </style>
            </head>
            <body>
                <div class="header">
                    <div style="font-size:0.8rem; opacity:0.6">EXPEDITION DASHBOARD</div>
                    <h1 style="margin:5px 0">{trip_name}</h1>
                    <div class="stats">
                        <div><div class="stat-val">₹{total:,.0f}</div><div class="stat-label">Total</div></div>
                        <div><div class="stat-val">₹{share:,.0f}</div><div class="stat-label">Share</div></div>
                        <div><div class="stat-val">{len(expenses)}</div><div class="stat-label">Txns</div></div>
                    </div>
                </div>
                <div class="container">
                    <div class="section-title">Settlement Status</div>
                    {settlement_html}
                    <div class="section-title">Expense Timeline</div>
                    {timeline_html}
                </div>
            </body>
        </html>"""
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return "<h1>⚠️ Dashboard Error</h1>"