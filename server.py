import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)
from sqlalchemy import select
from dotenv import load_dotenv

# 🛠️ SCHEDULER
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from bot.database.db_config import init_db, AsyncSessionLocal, Expense, User, TripGroup
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
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    # --- ADD THIS SAFETY NET ---
    if not TOKEN:
        logger.critical("❌ FATAL: TELEGRAM_BOT_TOKEN is missing! Bot cannot start.")
        yield  # Let the web dashboard boot up so you can see the server is alive
        return # Stop Telegram bot from crashing the rest of the app
    # ---------------------------
    bot_app = Application.builder().token(TOKEN).build()

    # --- HANDLER REGISTRATION ---
    bot_app.add_handler(CommandHandler("paid", record_expense))
    bot_app.add_handler(CommandHandler("balance", check_balance))
    bot_app.add_handler(CommandHandler("set_members", set_members))
    bot_app.add_handler(CommandHandler("export", export_expenses))

    bot_app.add_handler(CommandHandler("plan_trip", plan_trip))
    bot_app.add_handler(CommandHandler("weather", get_weather))
    bot_app.add_handler(CommandHandler("whereis", where_is_everyone))
    bot_app.add_handler(MessageHandler(filters.LOCATION, track_location))

    bot_app.add_handler(CommandHandler("explore", explore_nearby))
    bot_app.add_handler(CommandHandler("plan", show_plan))
    bot_app.add_handler(CommandHandler("set_plan", set_plan))
    bot_app.add_handler(CommandHandler("add_landmark", add_landmark))
    bot_app.add_handler(CommandHandler("sos", sos_emergency))
    bot_app.add_handler(CommandHandler("packing", packing_list))
    bot_app.add_handler(CommandHandler("gallery", trip_gallery))
    bot_app.add_handler(CommandHandler("set_gallery", set_gallery))

    bot_app.add_handler(MessageHandler(filters.Caption(["#vault", "#doc"]), save_to_vault))
    bot_app.add_handler(CommandHandler("vault", open_vault))

    bot_app.add_handler(CallbackQueryHandler(handle_expense_callback, pattern="^(appv_|rejt_)"))
    bot_app.add_handler(CallbackQueryHandler(handle_sos_callback, pattern="^sos_"))
    bot_app.add_handler(CallbackQueryHandler(handle_packing_callback, pattern="^pack_"))
    bot_app.add_handler(CallbackQueryHandler(get_vault_file, pattern="^getv_"))

    # Start Bot (POLLING MODE ONLY - NO WEBHOOK CONFLICTS)
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
    """Broadcast daily morning alert to all active trip groups."""
    logger.info("🌅 Generating daily briefings...")
    try:
        async with AsyncSessionLocal() as session:
            # Fetch all active trip groups
            groups = (await session.execute(select(TripGroup))).scalars().all()
            
        for group in groups:
            trip_name = group.trip_name if group.trip_name else "Expedition"
            msg = (
                f"🌅 <b>Good Morning Squad! ({trip_name})</b>\n\n"
                f"Rise and shine! Time to gear up for the day.\n"
                f"• Use <code>/weather</code> to check the conditions.\n"
                f"• Use <code>/plan</code> to see today's itinerary.\n"
                f"• Use <code>/packing</code> to verify your gear."
            )
            try:
                await bot_app.bot.send_message(chat_id=group.chat_id, text=msg, parse_mode='HTML')
                logger.info(f"✅ Briefing sent to {group.chat_id}")
            except Exception as e:
                logger.error(f"Failed to send briefing to {group.chat_id}: {e}")
    except Exception as e:
        logger.error(f"Briefing database error: {e}")

@app.get("/health")
async def health_check():
    """Endpoint for UptimeRobot to ping every 5 minutes."""
    return {"status": "healthy", "service": "yatra-bot"}

@app.get("/", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def dashboard(request: Request, chat_id: int = Query(None)):
    """Professional Multi-tenant Expense Dashboard with Premium UI."""
    try:
        async with AsyncSessionLocal() as session:
            if not chat_id:
                stmt = select(TripGroup).order_by(TripGroup.created_at.desc()).limit(1)
                latest_group = (await session.execute(stmt)).scalar()
                chat_id = latest_group.chat_id if latest_group else 0

            group = await session.get(TripGroup, chat_id)
            trip_name = group.trip_name if group else "Kedarnath Expedition"
            group_size = group.member_count if (group and group.member_count) else 1

            expense_stmt = (
                select(Expense, User.name)
                .join(User, Expense.payer_id == User.telegram_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
                .order_by(Expense.created_at.desc())
            )
            expenses = (await session.execute(expense_stmt)).all()

        total = sum([exp.Expense.amount for exp in expenses])
        share = total / group_size if group_size > 0 else 0

        # Generate Settlement Grid
        user_sums = {}
        for row in expenses:
            user_sums[row.name] = user_sums.get(row.name, 0) + row.Expense.amount
        
        settlement_html = ""
        for name, paid in user_sums.items():
            diff = paid - share
            status_class = "amt-plus" if diff >= 0 else "amt-minus"
            status_text = "Gets back" if diff >= 0 else "Owes"
            icon = "🟢" if diff >= 0 else "🔴"
            settlement_html += f"""
            <div class='s-card'>
                <div class='s-info'>
                    <h3>{name}</h3>
                    <p>Paid ₹{paid:,.0f} total</p>
                </div>
                <div class='s-amt {status_class}'>
                    <div style="font-size:0.7rem; color:#94A3B8; font-weight:normal;">{status_text}</div>
                    {icon} ₹{abs(diff):,.0f}
                </div>
            </div>"""

        # Generate Timeline HTML
        timeline_html = ""
        for row in expenses:
            exp = row.Expense
            dt = exp.created_at.strftime("%d %b · %I:%M %p")
            timeline_html += f"""
            <div class='t-item'>
                <div class='t-icon'>💸</div>
                <div class='t-details'>
                    <div class='t-head'>
                        <span class='t-who'>{row.name}</span>
                        <span class='t-cost'>₹{exp.amount:,.0f}</span>
                    </div>
                    <div class='t-desc'>{exp.description}</div>
                    <span class='t-time'>{dt}</span>
                </div>
            </div>"""

        # Premium HTML/CSS
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>Trip OS | {trip_name}</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {{
                    --bg: #0B1120;
                    --surface: #1E293B;
                    --primary: #38BDF8;
                    --text: #F8FAFC;
                    --text-muted: #94A3B8;
                    --success: #34D399;
                    --danger: #F87171;
                }}
                * {{ box-sizing: border-box; font-family: 'Inter', sans-serif; }}
                body {{ background: var(--bg); color: var(--text); margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }}
                .app-container {{ max-width: 600px; margin: 0 auto; padding: 20px; padding-bottom: 40px; animation: fadeIn 0.4s ease-out; }}
                
                header {{ text-align: center; margin-bottom: 24px; }}
                .trip-subtitle {{ color: var(--primary); font-size: 0.75rem; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px; }}
                .trip-title {{ font-size: 1.5rem; font-weight: 800; margin: 4px 0 0 0; letter-spacing: -0.5px; }}
                
                .hero-card {{ background: linear-gradient(145deg, #1E293B, #0F172A); border: 1px solid #334155; border-radius: 24px; padding: 24px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); margin-bottom: 32px; }}
                .hero-label {{ color: var(--text-muted); font-size: 0.85rem; font-weight: 600; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }}
                .hero-amount {{ font-size: 3.2rem; font-weight: 800; color: var(--primary); margin: 0; letter-spacing: -1px; line-height: 1; }}
                .hero-split {{ display: flex; justify-content: space-between; margin-top: 24px; padding-top: 24px; border-top: 1px solid #334155; }}
                .split-val {{ font-size: 1.2rem; font-weight: 800; color: var(--text); }}
                .split-lbl {{ font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}

                h2 {{ font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; margin: 0 0 16px 0; }}

                .s-card {{ background: var(--surface); border-radius: 16px; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border: 1px solid transparent; }}
                .s-info h3 {{ margin: 0 0 4px 0; font-size: 1rem; }}
                .s-info p {{ margin: 0; font-size: 0.8rem; color: var(--text-muted); }}
                .s-amt {{ font-weight: 800; font-size: 1.1rem; text-align: right; }}
                .amt-plus {{ color: var(--success); }}
                .amt-minus {{ color: var(--danger); }}

                .t-item {{ background: var(--surface); padding: 16px; border-radius: 16px; display: flex; gap: 16px; margin-bottom: 12px; align-items: flex-start; }}
                .t-icon {{ background: #334155; width: 42px; height: 42px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; flex-shrink: 0; }}
                .t-details {{ flex: 1; }}
                .t-head {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
                .t-who {{ font-weight: 600; font-size: 0.95rem; }}
                .t-cost {{ font-weight: 800; color: var(--text); }}
                .t-desc {{ color: var(--text-muted); font-size: 0.85rem; line-height: 1.4; }}
                .t-time {{ font-size: 0.7rem; color: #64748B; margin-top: 8px; display: block; }}

                .empty-state {{ text-align: center; color: var(--text-muted); padding: 20px; font-size: 0.9rem; background: var(--surface); border-radius: 16px; }}

                @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            </style>
        </head>
        <body>
            <div class="app-container">
                <header>
                    <div class="trip-subtitle">Expedition Dashboard</div>
                    <h1 class="trip-title">{trip_name}</h1>
                </header>

                <div class="hero-card">
                    <div class="hero-label">Total Spent</div>
                    <div class="hero-amount">₹{total:,.0f}</div>
                    <div class="hero-split">
                        <div style="text-align: left;">
                            <div class="split-val">₹{share:,.0f}</div>
                            <div class="split-lbl">Equal Share</div>
                        </div>
                        <div style="text-align: right;">
                            <div class="split-val">{len(expenses)}</div>
                            <div class="split-lbl">Transactions</div>
                        </div>
                    </div>
                </div>

                <h2>Settlements</h2>
                <div style="margin-bottom: 32px;">
                    {settlement_html if settlement_html else "<div class='empty-state'>No members have paid yet.</div>"}
                </div>

                <h2>Expense Timeline</h2>
                <div>
                    {timeline_html if timeline_html else "<div class='empty-state'>No expenses recorded. Use /paid in the bot.</div>"}
                </div>
            </div>
        </body>
        </html>"""
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return "<h1>⚠️ Dashboard Error. Please check bot logs.</h1>"