from sqlalchemy import select, func
import os
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from slowapi import Limiter
from slowapi.util import get_remote_address

from bot.database.db_config import AsyncSessionLocal, Expense, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("Dashboard")

# 1. Initialize the Router and Limiter for this specific file
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

# 2. Use @router instead of @app
@router.get("/", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def render_dashboard(request: Request, chat_id: int = Query(None)):
    """Professional Multi-tenant Expense Dashboard with Premium UI."""
    
    # 🚨 SECURITY PATCH: If no chat_id is in the URL, block access
    if not chat_id:
        return """
        <html>
            <body style='font-family: sans-serif; text-align: center; margin-top: 50px; background: #0B1120; color: #94A3B8;'>
                <h2>🔒 Access Denied</h2>
                <p>Please use the <code>/dashboard</code> command inside your Telegram group to get your secure link.</p>
            </body>
        </html>
        """

    try:
        async with AsyncSessionLocal() as session:
            group = await session.get(TripGroup, chat_id)
            
            if not group:
                return "<h1 style='color: white; text-align: center; margin-top: 50px;'>⚠️ Trip not found.</h1>"

            trip_name = group.trip_name if group else "Active Expedition"
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

        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>Trip OS | {trip_name}</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {{ --bg: #0B1120; --surface: #1E293B; --primary: #38BDF8; --text: #F8FAFC; --text-muted: #94A3B8; --success: #34D399; --danger: #F87171; }}
                * {{ box-sizing: border-box; font-family: 'Inter', sans-serif; }}
                body {{ background: var(--bg); color: var(--text); margin: 0; padding: 0; }}
                .app-container {{ max-width: 600px; margin: 0 auto; padding: 20px; animation: fadeIn 0.4s ease-out; }}
                header {{ text-align: center; margin-bottom: 24px; }}
                .trip-subtitle {{ color: var(--primary); font-size: 0.75rem; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px; }}
                .trip-title {{ font-size: 1.5rem; font-weight: 800; margin: 4px 0; }}
                .hero-card {{ background: linear-gradient(145deg, #1E293B, #0F172A); border: 1px solid #334155; border-radius: 24px; padding: 24px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); margin-bottom: 32px; }}
                .hero-amount {{ font-size: 3.2rem; font-weight: 800; color: var(--primary); margin: 0; letter-spacing: -1px; }}
                .hero-split {{ display: flex; justify-content: space-between; margin-top: 24px; padding-top: 24px; border-top: 1px solid #334155; }}
                .s-card {{ background: var(--surface); border-radius: 16px; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
                .amt-plus {{ color: var(--success); }}
                .amt-minus {{ color: var(--danger); }}
                .t-item {{ background: var(--surface); padding: 16px; border-radius: 16px; display: flex; gap: 16px; margin-bottom: 12px; }}
                .t-icon {{ background: #334155; width: 42px; height: 42px; border-radius: 50%; display: flex; align-items: center; justify-content: center; }}
                .t-details {{ flex: 1; }}
                .t-head {{ display: flex; justify-content: space-between; }}
                .t-time {{ font-size: 0.7rem; color: #64748B; margin-top: 8px; display: block; }}
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
                    <div style="color:var(--text-muted); font-size:0.85rem; font-weight:600; text-transform:uppercase;">Total Spent</div>
                    <div class="hero-amount">₹{total:,.0f}</div>
                    <div class="hero-split">
                        <div style="text-align: left;">
                            <div style="font-size:1.2rem; font-weight:800;">₹{share:,.0f}</div>
                            <div style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase;">Equal Share</div>
                        </div>
                        <div style="text-align: right;">
                            <div style="font-size:1.2rem; font-weight:800;">{len(expenses)}</div>
                            <div style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase;">Transactions</div>
                        </div>
                    </div>
                </div>
                <h2 style="font-size:0.85rem; color:var(--text-muted); text-transform:uppercase; margin-bottom:16px;">Settlements</h2>
                {settlement_html if settlement_html else "<div style='text-align:center; color:var(--text-muted); padding:20px; background:var(--surface); border-radius:16px;'>No data.</div>"}
                <h2 style="font-size:0.85rem; color:var(--text-muted); text-transform:uppercase; margin-top:32px; margin-bottom:16px;">Timeline</h2>
                {timeline_html if timeline_html else "<div style='text-align:center; color:var(--text-muted); padding:20px; background:var(--surface); border-radius:16px;'>No data.</div>"}
            </div>
        </body>
        </html>"""
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return "<h1>⚠️ Dashboard Error. Please check bot logs.</h1>"
    
@router.get("/dev/master", response_class=HTMLResponse)
async def superadmin_dashboard(request: Request, secret: str = Query(None)):
    """Developer 'God Mode' Dashboard showing all groups and global stats."""
    
    # 🚨 SECURITY: Check the secret token
    expected_secret = os.getenv("DEV_SECRET_KEY")
    if not expected_secret or secret != expected_secret:
        return """
        <html>
            <body style='font-family: sans-serif; text-align: center; margin-top: 50px; background: #0B1120; color: #F87171;'>
                <h2>🛑 SECURITY BREACH 🛑</h2>
                <p>Unauthorized Developer Access Attempt Logged.</p>
            </body>
        </html>
        """

    try:
        async with AsyncSessionLocal() as session:
            # 1. Global Stats
            total_users = (await session.execute(select(func.count(User.telegram_id)))).scalar() or 0
            total_groups = (await session.execute(select(func.count(TripGroup.chat_id)))).scalar() or 0
            total_money = (await session.execute(select(func.sum(Expense.amount)).where(Expense.is_verified == True))).scalar() or 0

            # 2. Fetch All Groups for a list
            groups = (await session.execute(select(TripGroup).order_by(TripGroup.created_at.desc()))).scalars().all()
            
            group_html = ""
            for g in groups:
                # Get total spent for this specific group
                group_spent = (await session.execute(select(func.sum(Expense.amount)).where(Expense.chat_id == g.chat_id, Expense.is_verified == True))).scalar() or 0
                
                group_html += f"""
                <div style="background: #1E293B; padding: 15px; border-radius: 10px; margin-bottom: 10px; border-left: 4px solid #A855F7; display: flex; justify-content: space-between;">
                    <div>
                        <h3 style="margin: 0; color: #F8FAFC; font-size: 1.1rem;">{g.trip_name}</h3>
                        <p style="margin: 4px 0 0 0; color: #94A3B8; font-size: 0.8rem;">Chat ID: {g.chat_id} | Members: {g.member_count or 'Unknown'}</p>
                    </div>
                    <div style="text-align: right;">
                        <h4 style="margin: 0; color: #34D399;">₹{group_spent:,.0f}</h4>
                        <a href="/?chat_id={g.chat_id}" target="_blank" style="color: #38BDF8; font-size: 0.8rem; text-decoration: none;">View Tenant UI ↗</a>
                    </div>
                </div>
                """

        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Dev God Mode | Trip OS</title>
            <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
            <style>
                body {{ background: #0B1120; color: #F8FAFC; font-family: 'JetBrains Mono', monospace; margin: 0; padding: 20px; }}
                .container {{ max-width: 800px; margin: 0 auto; }}
                h1 {{ color: #A855F7; border-bottom: 1px solid #334155; padding-bottom: 10px; }}
                .stats-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 30px; }}
                .stat-box {{ background: linear-gradient(145deg, #1E293B, #0F172A); padding: 20px; border-radius: 12px; border: 1px solid #334155; text-align: center; }}
                .stat-val {{ font-size: 2rem; font-weight: 700; color: #A855F7; margin: 0; }}
                .stat-lbl {{ font-size: 0.8rem; color: #94A3B8; text-transform: uppercase; margin-top: 5px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>👨‍💻 System Overview</h1>
                
                <div class="stats-grid">
                    <div class="stat-box">
                        <div class="stat-val">{total_users}</div>
                        <div class="stat-lbl">Total Users</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{total_groups}</div>
                        <div class="stat-lbl">Active Expeditions</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">₹{total_money:,.0f}</div>
                        <div class="stat-lbl">Total Volume</div>
                    </div>
                </div>

                <h2>🗄️ Database Tenants (Groups)</h2>
                {group_html if group_html else "<p style='color: #94A3B8;'>No groups exist in the database yet.</p>"}
            </div>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Dev Dashboard Error: {e}")
        return "<h1>⚠️ Database Error</h1>"