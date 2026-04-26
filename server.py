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

# 🛠️ NEW SCHEDULER IMPORTS
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
init_sentry()

logger = setup_logger("MasterServer")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "Kedarnath2026SecureToken") # 🛠️ NEW

limiter = Limiter(key_func=get_remote_address)

bot_app = Application.builder().token(TOKEN).updater(None).build()

# ==========================================
# NEW: AUTOMATED DAILY BRIEFING
# ==========================================
async def send_daily_briefing():
    logger.info("☀️ Running Daily Briefing...")
    async with AsyncSessionLocal() as session:
        groups = (await session.execute(select(TripGroup))).scalars().all()
        for group in groups:
            plan = (await session.execute(select(TripPlan).where(TripPlan.chat_id == group.chat_id))).scalar_one_or_none()
            plan_text = plan.plan_text if plan else "No plan set for today. /set_plan"
            
            # Formatting brief
            brief = (
                f"🌅 <b>GOOD MORNING SQUAD!</b>\n"
                f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
                f"🗺️ <b>Today's Directive:</b>\n{plan_text}\n\n"
                f"<i>Don't forget to check /weather and run /balance to settle dues. Stay safe!</i>"
            )
            try:
                await bot_app.bot.send_message(chat_id=group.chat_id, text=brief, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Briefing fail for {group.chat_id}: {e}")

scheduler = AsyncIOScheduler()
# Set to run every day at 7:00 AM IST
scheduler.add_job(send_daily_briefing, CronTrigger(hour=7, minute=0, timezone=ZoneInfo("Asia/Kolkata")))


# ==========================================
# GLOBAL ERROR HANDLER
# ==========================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    import sentry_sdk
    sentry_sdk.capture_exception(context.error)
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("⚠️ Something went wrong on our end. Please try again in a moment.")

# ==========================================
# START HANDLER
# ==========================================
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(pg_insert(User).values(telegram_id=user.id, name=user.full_name, username=user.username).on_conflict_do_nothing(index_elements=['telegram_id']))
                if chat.type != 'private':
                    await session.execute(pg_insert(TripGroup).values(chat_id=chat.id, trip_name=chat.title).on_conflict_do_nothing(index_elements=['chat_id']))
    except Exception as e:
        logger.error(f"start_handler DB error: {e}")
        await update.message.reply_text("⚠️ Registration hiccup. Try /start again in a moment.")
        return

    if chat.type == 'private':
        await update.message.reply_text(f"👋 Hey {user.first_name}! You're registered.\nAdd me to your trip group and use /start there.")
    else:
        await update.message.reply_text(f"🏔️ <b>Trip OS Active!</b>\nWelcome {user.first_name}.\nUse /help to see all commands.", parse_mode='HTML')

# ==========================================
# HELP HANDLER
# ==========================================
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏔️ <b>Trip OS — Command Guide</b>\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "👤 <b>Setup</b>\n"
        "/start — Register yourself\n"
        "/setmembers 12 — Set group size\n"
        "/packing — Shared Checklist\n\n"
        "💰 <b>Expenses</b>\n"
        "/paid 500 dinner — Log expense\n"
        "/balance — See who owes who\n"
        "/export — Export to CSV (Admin)\n\n"
        "📍 <b>Location & Plan</b>\n"
        "/whereis — See squad locations\n"
        "/set_trip 30.73,79.06 Name — Set destination\n"
        "/plan — View trip plan\n\n"
        "🗄️ <b>Vault</b>\n"
        "Send file with <b>#vault</b> to save it\n"
        "/gallery — View photos\n"
        "/vault — Access trip docs\n\n"
        "🚨 <b>Emergency</b>\n"
        "/sos — Broadcast your location\n",
        parse_mode='HTML'
    )

# ==========================================
# LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    async def boot_db():
        try:
            logger.info("🗄️ Connecting to database...")
            await init_db()
            logger.info("✅ Database ready.")
        except Exception as e:
            logger.error(f"🚨 Database init failed: {e}")

    asyncio.create_task(boot_db())
    
    # Pre-populate Command Menu globally
    commands = [
        ("start", "Register yourself"),
        ("help", "See all commands"),
        ("paid", "<amt> <desc> Log expense"),
        ("balance", "See settlement dues"),
        ("packing", "Shared Checklist"),
        ("export", "Download CSV ledger"),
        ("whereis", "Squad locations"),
        ("sos", "Emergency broadcast"),
        ("set_trip", "<lat,lon> <name> Set dest"),
        ("weather", "Destination weather"),
        ("plan", "View itinerary"),
        ("gallery", "View photos"),
        ("vault", "Access docs")
    ]
    await bot_app.bot.set_my_commands(commands)

    bot_app.add_handler(CommandHandler("start", start_handler))
    bot_app.add_handler(CommandHandler("help", help_handler))
    bot_app.add_handler(CommandHandler("paid", record_expense))
    bot_app.add_handler(CommandHandler("balance", check_balance))
    bot_app.add_handler(CommandHandler("setmembers", set_members))
    bot_app.add_handler(CommandHandler("export", export_expenses)) # 🛠️ NEW
    bot_app.add_handler(CommandHandler("set_trip", plan_trip)) # Beautified alias
    bot_app.add_handler(CommandHandler("plan_trip", plan_trip))
    bot_app.add_handler(CommandHandler("weather", get_weather))
    bot_app.add_handler(CommandHandler("whereis", where_is_everyone))
    bot_app.add_handler(CommandHandler("explore", explore_nearby))
    bot_app.add_handler(CommandHandler("plan", show_plan))
    bot_app.add_handler(CommandHandler("set_plan", set_plan))
    bot_app.add_handler(CommandHandler("add_landmark", add_landmark))
    bot_app.add_handler(CommandHandler("gallery", trip_gallery))
    bot_app.add_handler(CommandHandler("set_gallery", set_gallery))
    bot_app.add_handler(CommandHandler("vault", open_vault))
    bot_app.add_handler(CommandHandler("packing", packing_list)) # 🛠️ NEW
    bot_app.add_handler(CommandHandler("sos", sos_emergency))

    # Callbacks
    bot_app.add_handler(CallbackQueryHandler(handle_expense_callback, pattern="^(appv|rejt)_"))
    bot_app.add_handler(CallbackQueryHandler(handle_sos_callback, pattern="^sos_"))
    bot_app.add_handler(CallbackQueryHandler(handle_packing_callback, pattern="^pack_")) # 🛠️ NEW

    # Messages
    bot_app.add_handler(MessageHandler(filters.LOCATION, track_location))
    bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_to_vault))
    bot_app.add_error_handler(error_handler)

    await bot_app.initialize()
    await bot_app.start()
    
    # Start Cron Jobs
    scheduler.start() 

    if WEBHOOK_URL:
        webhook_path = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        # 🛠️ FIX: Enforce Secret Token Security
        await bot_app.bot.set_webhook(url=webhook_path, secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
        logger.info(f"✅ Webhook secured at: {webhook_path}")

    yield

    scheduler.shutdown()
    await bot_app.stop()
    await bot_app.shutdown()


app = FastAPI(title="Trip OS", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


@app.post("/webhook")
@limiter.limit("60/minute")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    # 🛠️ FIX: Validate Telegram Signature to prevent hacking
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        logger.warning("🚨 Blocked unauthorized webhook ping!")
        return {"status": "unauthorized"}

    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    background_tasks.add_task(bot_app.process_update, update)
    return {"status": "ok"}

@app.get("/health")
async def health_check():
    return {"status": "alive"}

# ==========================================
# DASHBOARD (UNTOUCHED HTML)
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(chat_id: int = Query(None)):
    try:
        async with AsyncSessionLocal() as session:
            if not chat_id:
                groups_result = await session.execute(select(TripGroup))
                groups = groups_result.scalars().all()

                group_cards = ""
                for g in groups:
                    count = (await session.execute(
                        select(func.count(Expense.id))
                        .where(Expense.chat_id == g.chat_id, Expense.is_verified == True)
                    )).scalar() or 0
                    total = (await session.execute(
                        select(func.sum(Expense.amount))
                        .where(Expense.chat_id == g.chat_id, Expense.is_verified == True)
                    )).scalar() or 0
                    group_cards += f"""
                    <a href="/?chat_id={g.chat_id}" class="group-card">
                        <div class="group-icon">🏔️</div>
                        <div class="group-info">
                            <div class="group-name">{g.trip_name or 'Trip Group'}</div>
                            <div class="group-meta">{count} expenses · ₹{total:,.0f} spent</div>
                        </div>
                        <div class="group-arrow">→</div>
                    </a>"""

                return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trip OS</title>
    <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --peak:#0f1923; --ridge:#1a2d3d; --glacier:#243b55;
            --snow:#e8f4f8; --ice:#a8d8ea; --pine:#2ecc71;
            --mist:rgba(168,216,234,0.07); --border:rgba(168,216,234,0.1);
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ background:var(--peak); color:var(--snow); font-family:'DM Sans',sans-serif; min-height:100vh; }}
        .masthead {{ padding:48px 24px 32px; text-align:center; border-bottom:1px solid var(--border); }}
        .logo {{ font-family:'Bebas Neue',cursive; font-size:clamp(2.5rem,6vw,4rem); letter-spacing:4px; color:var(--ice); }}
        .tagline {{ color:rgba(168,216,234,0.4); font-size:0.82rem; letter-spacing:3px; text-transform:uppercase; margin-top:8px; }}
        .container {{ max-width:680px; margin:0 auto; padding:40px 20px; }}
        .section-title {{ font-family:'Bebas Neue',cursive; font-size:1.3rem; letter-spacing:3px; color:var(--ice); margin-bottom:20px; opacity:0.6; }}
        .group-card {{ display:flex; align-items:center; gap:16px; background:var(--mist); border:1px solid var(--border); border-radius:16px; padding:20px 24px; margin-bottom:12px; text-decoration:none; color:var(--snow); transition:all 0.2s ease; }}
        .group-card:hover {{ background:rgba(168,216,234,0.12); transform:translateX(4px); }}
        .group-icon {{ font-size:2rem; }}
        .group-info {{ flex:1; }}
        .group-name {{ font-weight:600; font-size:1.05rem; }}
        .group-meta {{ color:rgba(168,216,234,0.45); font-size:0.82rem; margin-top:4px; }}
        .group-arrow {{ color:var(--ice); opacity:0.35; font-size:1.2rem; }}
        .empty {{ text-align:center; padding:60px 20px; color:rgba(168,216,234,0.25); line-height:1.8; }}
    </style>
</head>
<body>
    <div class="masthead">
        <div class="logo">⛰ TRIP OS</div>
        <div class="tagline">Command Center · Select Your Expedition</div>
    </div>
    <div class="container">
        <div class="section-title">Active Expeditions</div>
        {group_cards if group_cards else '<div class="empty">No trips yet.<br>Add the bot to a group and use /start.</div>'}
    </div>
</body>
</html>"""

            group_res = await session.get(TripGroup, chat_id)
            trip_name = group_res.trip_name if group_res else f"Group {chat_id}"

            expenses = (await session.execute(
                select(Expense, User.name)
                .join(User, Expense.payer_id == User.telegram_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
                .order_by(Expense.created_at.desc())
            )).all()

            per_person = (await session.execute(
                select(User.name, func.sum(Expense.amount), func.count(Expense.id))
                .join(Expense, User.telegram_id == Expense.payer_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
                .group_by(User.name)
                .order_by(func.sum(Expense.amount).desc())
            )).all()

        total = sum(r.Expense.amount for r in expenses)
        member_count = group_res.member_count if (group_res and group_res.member_count > 0) else len(per_person)
        share = total / member_count if member_count > 0 else 0
        avg = total / len(expenses) if expenses else 0

        settlement_html = ""
        for name, paid, count in per_person:
            diff = paid - share
            if diff > 1:
                status_class, status_text, icon = "positive", f"gets back ₹{abs(diff):,.0f}", "↑"
            elif diff < -1:
                status_class, status_text, icon = "negative", f"owes ₹{abs(diff):,.0f}", "↓"
            else:
                status_class, status_text, icon = "neutral", "settled up", "="
            pct = (paid / total * 100) if total > 0 else 0
            settlement_html += f"""
            <div class="member-card">
                <div class="member-header">
                    <div class="member-avatar">{name[0].upper()}</div>
                    <div class="member-info">
                        <div class="member-name">{name}</div>
                        <div class="member-count">{count} expense{'s' if count != 1 else ''}</div>
                    </div>
                    <div class="member-amount">₹{paid:,.0f}</div>
                </div>
                <div class="progress-bar"><div class="progress-fill" style="width:{pct:.1f}%"></div></div>
                <div class="member-status {status_class}"><span>{icon}</span> {status_text}</div>
            </div>"""

        timeline_html = ""
        for row in expenses[:20]:
            dt = row.Expense.created_at
            date_str = dt.strftime("%d %b, %I:%M %p") if dt else "—"
            timeline_html += f"""
            <div class="timeline-item">
                <div class="timeline-dot"></div>
                <div class="timeline-content">
                    <div class="timeline-header">
                        <span class="timeline-name">{row.name}</span>
                        <span class="timeline-amount">₹{row.Expense.amount:,.0f}</span>
                    </div>
                    <div class="timeline-desc">{row.Expense.description}</div>
                    <div class="timeline-time">{date_str}</div>
                </div>
            </div>"""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trip OS — {trip_name}</title>
    <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;1,400&display=swap" rel="stylesheet">
    <style>
        :root {{
            --peak:#0f1923; --ridge:#1a2d3d; --glacier:#243b55;
            --snow:#e8f4f8; --ice:#a8d8ea; --pine:#2ecc71;
            --amber:#f39c12; --danger:#e74c3c;
            --mist:rgba(168,216,234,0.07); --border:rgba(168,216,234,0.1);
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ background:var(--peak); color:var(--snow); font-family:'DM Sans',sans-serif; min-height:100vh; }}
        .masthead {{ background:linear-gradient(180deg,rgba(26,45,61,0.9) 0%,transparent 100%); padding:28px 24px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; }}
        .logo-group {{ display:flex; align-items:baseline; gap:12px; }}
        .logo {{ font-family:'Bebas Neue',cursive; font-size:clamp(1.8rem,5vw,2.6rem); letter-spacing:4px; color:var(--ice); }}
        .trip-badge {{ background:rgba(46,204,113,0.12); border:1px solid rgba(46,204,113,0.25); color:var(--pine); padding:4px 12px; border-radius:20px; font-size:0.75rem; font-weight:600; letter-spacing:1px; text-transform:uppercase; }}
        .back-link {{ color:var(--ice); opacity:0.45; font-size:0.82rem; text-decoration:none; }}
        .hero {{ padding:40px 24px; text-align:center; }}
        .total-label {{ font-size:0.78rem; letter-spacing:4px; text-transform:uppercase; color:rgba(168,216,234,0.35); margin-bottom:8px; }}
        .total-amount {{ font-family:'Bebas Neue',cursive; font-size:clamp(3.5rem,10vw,6rem); color:var(--snow); letter-spacing:2px; line-height:1; }}
        .stats-row {{ display:flex; justify-content:center; gap:40px; margin-top:32px; flex-wrap:wrap; }}
        .stat {{ text-align:center; }}
        .stat-value {{ font-family:'Bebas Neue',cursive; font-size:1.8rem; color:var(--ice); }}
        .stat-label {{ font-size:0.7rem; letter-spacing:2px; text-transform:uppercase; color:rgba(168,216,234,0.3); margin-top:2px; }}
        .container {{ max-width:900px; margin:0 auto; padding:0 20px 60px; }}
        .section {{ margin-bottom:40px; }}
        .section-header {{ display:flex; align-items:center; gap:12px; margin-bottom:20px; padding-bottom:12px; border-bottom:1px solid var(--border); }}
        .section-title {{ font-family:'Bebas Neue',cursive; font-size:1.3rem; letter-spacing:3px; color:var(--ice); opacity:0.75; }}
        .share-card {{ background:linear-gradient(135deg,rgba(46,204,113,0.08),rgba(36,59,85,0.5)); border:1px solid rgba(46,204,113,0.18); border-radius:16px; padding:24px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:16px; margin-bottom:24px; }}
        .share-label {{ font-size:0.75rem; letter-spacing:2px; text-transform:uppercase; color:rgba(46,204,113,0.55); margin-bottom:4px; }}
        .share-amount {{ font-family:'Bebas Neue',cursive; font-size:2.2rem; color:var(--pine); }}
        .members-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(270px,1fr)); gap:16px; }}
        .member-card {{ background:var(--mist); border:1px solid var(--border); border-radius:16px; padding:20px; }}
        .member-header {{ display:flex; align-items:center; gap:14px; margin-bottom:14px; }}
        .member-avatar {{ width:42px; height:42px; border-radius:50%; background:linear-gradient(135deg,var(--glacier),var(--ridge)); border:1px solid var(--border); display:flex; align-items:center; justify-content:center; font-weight:700; font-size:1rem; color:var(--ice); flex-shrink:0; }}
        .member-info {{ flex:1; min-width:0; }}
        .member-name {{ font-weight:600; font-size:0.95rem; }}
        .member-count {{ font-size:0.72rem; color:rgba(168,216,234,0.35); margin-top:2px; }}
        .member-amount {{ font-family:'Bebas Neue',cursive; font-size:1.4rem; color:var(--snow); }}
        .progress-bar {{ height:3px; background:rgba(168,216,234,0.08); border-radius:2px; margin-bottom:12px; }}
        .progress-fill {{ height:100%; background:linear-gradient(90deg,var(--ice),var(--pine)); border-radius:2px; }}
        .member-status {{ font-size:0.78rem; font-weight:500; display:flex; align-items:center; gap:6px; }}
        .positive {{ color:var(--pine); }} .negative {{ color:var(--danger); }} .neutral {{ color:rgba(168,216,234,0.35); }}
        .timeline {{ position:relative; padding-left:24px; }}
        .timeline::before {{ content:''; position:absolute; left:6px; top:8px; bottom:8px; width:1px; background:var(--border); }}
        .timeline-item {{ position:relative; margin-bottom:22px; }}
        .timeline-dot {{ position:absolute; left:-21px; top:16px; width:10px; height:10px; border-radius:50%; background:var(--glacier); border:2px solid var(--ice); }}
        .timeline-content {{ background:var(--mist); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }}
        .timeline-header {{ display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:4px; }}
        .timeline-name {{ font-weight:600; font-size:0.88rem; }}
        .timeline-amount {{ font-family:'Bebas Neue',cursive; font-size:1.1rem; color:var(--amber); }}
        .timeline-desc {{ font-size:0.8rem; color:rgba(168,216,234,0.45); margin-bottom:4px; }}
        .timeline-time {{ font-size:0.7rem; color:rgba(168,216,234,0.22); }}
        .empty {{ text-align:center; padding:48px 20px; color:rgba(168,216,234,0.22); font-size:0.88rem; line-height:1.8; }}
        .footer {{ text-align:center; padding:32px 20px 16px; color:rgba(168,216,234,0.12); font-size:0.72rem; letter-spacing:2px; }}
        @media(max-width:600px) {{ .masthead{{padding:16px;}} .hero{{padding:28px 16px;}} .stats-row{{gap:20px;}} .members-grid{{grid-template-columns:1fr;}} }}
    </style>
</head>
<body>
    <div class="masthead">
        <div class="logo-group">
            <div class="logo">⛰ TRIP OS</div>
            <div class="trip-badge">{trip_name}</div>
        </div>
        <a href="/" class="back-link">← All Trips</a>
    </div>
    <div class="hero">
        <div class="total-label">Total Expedition Spend</div>
        <div class="total-amount">₹{total:,.0f}</div>
        <div class="stats-row">
            <div class="stat"><div class="stat-value">{member_count}</div><div class="stat-label">Members</div></div>
            <div class="stat"><div class="stat-value">{len(expenses)}</div><div class="stat-label">Expenses</div></div>
            <div class="stat"><div class="stat-value">₹{avg:,.0f}</div><div class="stat-label">Avg</div></div>
            <div class="stat"><div class="stat-value">₹{share:,.0f}</div><div class="stat-label">Each Pays</div></div>
        </div>
    </div>
    <div class="container">
        <div class="section">
            <div class="section-header"><span>⚖️</span><div class="section-title">Settlement</div></div>
            <div class="share-card">
                <div>
                    <div class="share-label">Equal Share</div>
                    <div class="share-amount">₹{share:,.0f}</div>
                </div>
                <div style="font-size:2.5rem">🧾</div>
            </div>
            <div class="members-grid">
                {settlement_html or '<div class="empty">No expenses yet.</div>'}
            </div>
        </div>
        <div class="section">
            <div class="section-header"><span>📋</span><div class="section-title">Timeline</div></div>
            <div class="timeline">
                {timeline_html or '<div class="empty">No expenses yet.<br>Use /paid in the group.</div>'}
            </div>
        </div>
    </div>
    <div class="footer">TRIP OS · {trip_name.upper()}</div>
</body>
</html>"""
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return "<html><body style='background:#0f1923;color:#a8d8ea;text-align:center;padding:60px;font-family:sans-serif'><h1>⛰</h1><h2>Dashboard offline</h2></body></html>"