import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import HTMLResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from dotenv import load_dotenv

from bot.database.db_config import init_db, AsyncSessionLocal, Expense, User, TripGroup
from bot.handlers.expenses import record_expense, handle_expense_callback, check_balance
from bot.handlers.logistics import track_location, get_weather, plan_trip, where_is_everyone
from bot.handlers.itinerary import (
    explore_nearby, show_plan, sos_emergency, handle_sos_callback, add_landmark, set_plan
)
from bot.handlers.vault import save_to_vault, open_vault, get_vault_file
from bot.utils.logger import setup_logger

load_dotenv()
logger = setup_logger("MasterServer")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

bot_app = Application.builder().token(TOKEN).updater(None).build()

# ==========================================
# GLOBAL ERROR HANDLER
# ==========================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Unhandled exception: {context.error}")
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("⚠️ Something went wrong, please try again in a moment.")

# ==========================================
# START HANDLER
# ==========================================
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    # 🏔️ RESILIENCE: Retry loop for unstable mountain networks
    for attempt in range(3):
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # 1. Register User
                    await session.execute(pg_insert(User).values(
                        telegram_id=user.id,
                        name=user.full_name,
                        username=user.username
                    ).on_conflict_do_nothing(index_elements=['telegram_id']))

                    # 2. Register Group
                    if chat.type != 'private':
                        await session.execute(pg_insert(TripGroup).values(
                            chat_id=chat.id,
                            trip_name=chat.title
                        ).on_conflict_do_nothing(index_elements=['chat_id']))
                    
                    await session.flush()
                break  # Success! Exit retry loop
        except Exception as e:
            logger.error(f"Start handler attempt {attempt+1} failed: {e}")
            if attempt == 2:  # All retries failed
                if update.message:
                    await update.message.reply_text("⚠️ Database busy. Please try /start again in 5 seconds.")
                return
            await asyncio.sleep(1)  # Wait 1s before retrying

    await update.message.reply_text(
        f"🏔️ Welcome to Trip OS, {user.first_name}!\n\n"
        f"Commands:\n"
        f"/paid <amount> <desc> — Log an expense\n"
        f"/balance — See who owes what\n"
        f"/whereis — Squad locations\n"
        f"/sos — Emergency broadcast\n"
        f"/explore — Nearby places\n"
        f"/vault — Trip documents"
    )

    
# ==========================================
# LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    async def boot_db():
        try:
            logger.info("🗄️ Connecting to Supabase...")
            await init_db()
            logger.info("✅ Database Connected Successfully!")
        except Exception as e:
            logger.error(f"🚨 Database Connection Failed: {e}")

    asyncio.create_task(boot_db())

    # Handler Registration
    bot_app.add_handler(CommandHandler("start", start_handler))
    bot_app.add_handler(CommandHandler("paid", record_expense))
    bot_app.add_handler(CommandHandler("balance", check_balance))
    
    # 🛠️ THE FIX: Specific patterns for distinct callback handlers
    bot_app.add_handler(CallbackQueryHandler(handle_expense_callback, pattern="^(appv|rejt)_"))
    bot_app.add_handler(CallbackQueryHandler(handle_sos_callback, pattern="^sos_confirm"))

    bot_app.add_handler(CommandHandler("plan_trip", plan_trip))
    bot_app.add_handler(CommandHandler("weather", get_weather))
    bot_app.add_handler(CommandHandler("whereis", where_is_everyone))
    bot_app.add_handler(CommandHandler("sos", sos_emergency))
    bot_app.add_handler(MessageHandler(filters.LOCATION, track_location))
    bot_app.add_handler(CommandHandler("explore", explore_nearby))
    bot_app.add_handler(CommandHandler("plan", show_plan))
    
    # Under-construction feature redirects
    bot_app.add_handler(CommandHandler("gallery", sos_emergency))
    bot_app.add_handler(CommandHandler("set_gallery", sos_emergency))
    
    bot_app.add_handler(CommandHandler("add_landmark", add_landmark))
    bot_app.add_handler(CommandHandler("set_plan", set_plan))
    bot_app.add_handler(CommandHandler("vault", open_vault))
    bot_app.add_handler(CommandHandler("get", get_vault_file))
    bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_to_vault))

    bot_app.add_error_handler(error_handler)

    await bot_app.initialize()
    await bot_app.start()

    if WEBHOOK_URL:
        webhook_path = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        await bot_app.bot.set_webhook(url=webhook_path, drop_pending_updates=True)
        logger.info(f"✅ Webhook locked to: {webhook_path}")

    yield
    await bot_app.stop()
    await bot_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    background_tasks.add_task(bot_app.process_update, update)
    return {"status": "ok"}

@app.get("/health")
async def health_check():
    return {"status": "alive", "engine": "FastAPI"}

# ==========================================
# DASHBOARD (HTML RESPONSE)
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
                    expense_count = await session.execute(
                        select(func.count(Expense.id))
                        .where(Expense.chat_id == g.chat_id, Expense.is_verified == True)
                    )
                    count = expense_count.scalar() or 0
                    total_res = await session.execute(
                        select(func.sum(Expense.amount))
                        .where(Expense.chat_id == g.chat_id, Expense.is_verified == True)
                    )
                    total = total_res.scalar() or 0
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
    <title>Trip OS — Select Group</title>
    <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --peak:#0f1923; --ridge:#1a2d3d; --glacier:#243b55;
            --snow:#e8f4f8; --ice:#a8d8ea; --pine:#2ecc71;
            --amber:#f39c12; --danger:#e74c3c;
            --mist:rgba(168,216,234,0.07); --border:rgba(168,216,234,0.1);
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ background:var(--peak); color:var(--snow); font-family:'DM Sans',sans-serif; min-height:100vh;
            background-image:radial-gradient(ellipse at 20% 50%,rgba(36,59,85,0.6) 0%,transparent 60%); }}
        .masthead {{ padding:48px 24px 32px; text-align:center; border-bottom:1px solid var(--border); }}
        .logo {{ font-family:'Bebas Neue',cursive; font-size:clamp(2.5rem,6vw,4rem); letter-spacing:4px; color:var(--ice); }}
        .tagline {{ color:rgba(168,216,234,0.4); font-size:0.82rem; letter-spacing:3px; text-transform:uppercase; margin-top:8px; }}
        .container {{ max-width:680px; margin:0 auto; padding:40px 20px; }}
        .section-title {{ font-family:'Bebas Neue',cursive; font-size:1.3rem; letter-spacing:3px; color:var(--ice); margin-bottom:20px; opacity:0.6; }}
        .group-card {{
            display:flex; align-items:center; gap:16px;
            background:var(--mist); border:1px solid var(--border);
            border-radius:16px; padding:20px 24px; margin-bottom:12px;
            text-decoration:none; color:var(--snow); transition:all 0.2s ease;
        }}
        .group-card:hover {{ background:rgba(168,216,234,0.12); border-color:rgba(168,216,234,0.25); transform:translateX(4px); }}
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
        {group_cards if group_cards else '<div class="empty">No trips recorded yet.<br>Add the bot to a group and use /start to register.</div>'}
    </div>
</body>
</html>"""

            group_res = await session.get(TripGroup, chat_id)
            trip_name = group_res.trip_name if group_res else f"Group {chat_id}"

            exp_result = await session.execute(
                select(Expense, User.name)
                .join(User, Expense.payer_id == User.telegram_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
                .order_by(Expense.created_at.desc())
            )
            expenses = exp_result.all()

            per_person_res = await session.execute(
                select(User.name, func.sum(Expense.amount), func.count(Expense.id))
                .join(Expense, User.telegram_id == Expense.payer_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
                .group_by(User.name)
                .order_by(func.sum(Expense.amount).desc())
            )
            per_person = per_person_res.all()

        total = sum(r.Expense.amount for r in expenses)
        num_people = len(per_person)
        share = total / num_people if num_people > 0 else 0
        avg = total / len(expenses) if len(expenses) > 0 else 0

        settlement_html = ""
        for name, paid, count in per_person:
            diff = paid - share
            if diff > 0:
                status_class, status_text, status_icon = "positive", f"gets back ₹{abs(diff):,.0f}", "↑"
            elif diff < 0:
                status_class, status_text, status_icon = "negative", f"owes ₹{abs(diff):,.0f}", "↓"
            else:
                status_class, status_text, status_icon = "neutral", "settled up", "="
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
                <div class="member-status {status_class}"><span class="status-icon">{status_icon}</span> {status_text}</div>
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
        body {{
            background:var(--peak); color:var(--snow); font-family:'DM Sans',sans-serif; min-height:100vh;
            background-image: radial-gradient(ellipse at 10% 0%,rgba(36,59,85,0.8) 0%,transparent 55%),
                              radial-gradient(ellipse at 90% 100%,rgba(46,204,113,0.04) 0%,transparent 50%);
        }}
        .masthead {{
            background:linear-gradient(180deg,rgba(26,45,61,0.9) 0%,transparent 100%);
            padding:28px 24px; border-bottom:1px solid var(--border);
            display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;
        }}
        .logo-group {{ display:flex; align-items:baseline; gap:12px; }}
        .logo {{ font-family:'Bebas Neue',cursive; font-size:clamp(1.8rem,5vw,2.6rem); letter-spacing:4px; color:var(--ice); }}
        .trip-badge {{ background:rgba(46,204,113,0.12); border:1px solid rgba(46,204,113,0.25); color:var(--pine); padding:4px 12px; border-radius:20px; font-size:0.75rem; font-weight:600; letter-spacing:1px; text-transform:uppercase; }}
        .back-link {{ color:var(--ice); opacity:0.45; font-size:0.82rem; text-decoration:none; transition:opacity 0.2s; }}
        .back-link:hover {{ opacity:1; }}
        .hero {{ padding:40px 24px; text-align:center; position:relative; overflow:hidden; }}
        .hero::before {{ content:'🏔️'; position:absolute; top:-20px; left:50%; transform:translateX(-50%); font-size:120px; opacity:0.03; pointer-events:none; }}
        .total-label {{ font-size:0.78rem; letter-spacing:4px; text-transform:uppercase; color:rgba(168,216,234,0.35); margin-bottom:8px; }}
        .total-amount {{ font-family:'Bebas Neue',cursive; font-size:clamp(3.5rem,10vw,6rem); color:var(--snow); letter-spacing:2px; line-height:1; }}
        .total-sub {{ color:rgba(168,216,234,0.35); font-size:0.82rem; margin-top:10px; }}
        .stats-row {{ display:flex; justify-content:center; gap:40px; margin-top:32px; flex-wrap:wrap; }}
        .stat {{ text-align:center; }}
        .stat-value {{ font-family:'Bebas Neue',cursive; font-size:1.8rem; color:var(--ice); }}
        .stat-label {{ font-size:0.7rem; letter-spacing:2px; text-transform:uppercase; color:rgba(168,216,234,0.3); margin-top:2px; }}
        .container {{ max-width:900px; margin:0 auto; padding:0 20px 60px; }}
        .section {{ margin-bottom:40px; animation:fadeUp 0.5s ease both; }}
        .section:nth-child(1){{ animation-delay:0.1s; }}
        .section:nth-child(2){{ animation-delay:0.2s; }}
        .section-header {{ display:flex; align-items:center; gap:12px; margin-bottom:20px; padding-bottom:12px; border-bottom:1px solid var(--border); }}
        .section-title {{ font-family:'Bebas Neue',cursive; font-size:1.3rem; letter-spacing:3px; color:var(--ice); opacity:0.75; }}
        .share-card {{
            background:linear-gradient(135deg,rgba(46,204,113,0.08),rgba(36,59,85,0.5));
            border:1px solid rgba(46,204,113,0.18); border-radius:16px; padding:24px;
            display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:16px; margin-bottom:24px;
        }}
        .share-label {{ font-size:0.75rem; letter-spacing:2px; text-transform:uppercase; color:rgba(46,204,113,0.55); margin-bottom:4px; }}
        .share-amount {{ font-family:'Bebas Neue',cursive; font-size:2.2rem; color:var(--pine); }}
        .share-desc {{ font-size:0.78rem; color:rgba(168,216,234,0.35); margin-top:4px; }}
        .members-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(270px,1fr)); gap:16px; }}
        .member-card {{ background:var(--mist); border:1px solid var(--border); border-radius:16px; padding:20px; transition:border-color 0.2s; }}
        .member-card:hover {{ border-color:rgba(168,216,234,0.2); }}
        .member-header {{ display:flex; align-items:center; gap:14px; margin-bottom:14px; }}
        .member-avatar {{ width:42px; height:42px; border-radius:50%; background:linear-gradient(135deg,var(--glacier),var(--ridge)); border:1px solid var(--border); display:flex; align-items:center; justify-content:center; font-weight:700; font-size:1rem; color:var(--ice); flex-shrink:0; }}
        .member-info {{ flex:1; min-width:0; }}
        .member-name {{ font-weight:600; font-size:0.95rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
        .member-count {{ font-size:0.72rem; color:rgba(168,216,234,0.35); margin-top:2px; }}
        .member-amount {{ font-family:'Bebas Neue',cursive; font-size:1.4rem; color:var(--snow); white-space:nowrap; }}
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
        .timeline-amount {{ font-family:'Bebas Neue',cursive; font-size:1.1rem; color:var(--amber); white-space:nowrap; }}
        .timeline-desc {{ font-size:0.8rem; color:rgba(168,216,234,0.45); margin-bottom:4px; }}
        .timeline-time {{ font-size:0.7rem; color:rgba(168,216,234,0.22); }}
        .footer {{ text-align:center; padding:32px 20px 16px; color:rgba(168,216,234,0.12); font-size:0.72rem; letter-spacing:2px; }}
        @keyframes fadeUp {{ from{{opacity:0;transform:translateY(16px);}} to{{opacity:1;transform:translateY(0);}} }}
        @media(max-width:600px){{
            .masthead{{padding:16px;}} .hero{{padding:28px 16px;}}
            .stats-row{{gap:20px;}} .members-grid{{grid-template-columns:1fr;}}
            .share-card{{flex-direction:column;}}
        }}
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
        <div class="total-sub">{len(expenses)} verified expenses · {num_people} member{'s' if num_people != 1 else ''}</div>
        <div class="stats-row">
            <div class="stat"><div class="stat-value">{num_people}</div><div class="stat-label">Members</div></div>
            <div class="stat"><div class="stat-value">{len(expenses)}</div><div class="stat-label">Expenses</div></div>
            <div class="stat"><div class="stat-value">₹{avg:,.0f}</div><div class="stat-label">Avg Spend</div></div>
            <div class="stat"><div class="stat-value">₹{share:,.0f}</div><div class="stat-label">Equal Share</div></div>
        </div>
    </div>
    <div class="container">
        <div class="section">
            <div class="section-header"><span>⚖️</span><div class="section-title">Settlement</div></div>
            <div class="share-card">
                <div>
                    <div class="share-label">Equal Share Per Person</div>
                    <div class="share-amount">₹{share:,.0f}</div>
                    <div class="share-desc">Split equally among {num_people} member{'s' if num_people != 1 else ''}</div>
                </div>
                <div style="font-size:2.5rem">🧾</div>
            </div>
            <div class="members-grid">
                {settlement_html if settlement_html else '<div class="empty">No expenses yet.</div>'}
            </div>
        </div>
        <div class="section">
            <div class="section-header"><span>📋</span><div class="section-title">Expense Timeline</div></div>
            <div class="timeline">
                {timeline_html if timeline_html else '<div class="empty">No expenses recorded yet.</div>'}
            </div>
        </div>
    </div>
    <div class="footer">TRIP OS · {trip_name.upper()} · POWERED BY YATRA BOT</div>
</body>
</html>"""

    except Exception as e:
        logger.error(f"Dashboard Error: {e}")
        return """<html><body style='background:#0f1923;color:#a8d8ea;font-family:sans-serif;text-align:center;padding:60px'>
            <h1 style='font-size:3rem'>⛰</h1><h2>Dashboard temporarily offline</h2>
            <p style='opacity:0.4;margin-top:8px'>Check server logs.</p>
        </body></html>"""