import datetime
from zoneinfo import ZoneInfo
from telegram.ext import ContextTypes, Application
from sqlalchemy import select
from bot.database.db_config import AsyncSessionLocal, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("Scheduler")

async def morning_briefing(context: ContextTypes.DEFAULT_TYPE):
    """Runs every day at 7:00 AM to send morning updates to all active trips."""
    logger.info("🌅 Running 7 AM Morning Briefing...")
    
    try:
        async with AsyncSessionLocal() as session:
            # Fetch all registered groups from the database
            groups = (await session.execute(select(TripGroup))).scalars().all()
            
            for group in groups:
                try:
                    # You can customize this message!
                    msg = f"🌅 <b>Good Morning, Expedition!</b>\n\n"
                    msg += "It's 7:00 AM. Don't forget to check your <code>/weather</code> and <code>/packing</code> list for today's adventures! 🎒🗺️"
                    
                    await context.bot.send_message(
                        chat_id=group.chat_id, 
                        text=msg, 
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"Failed to send briefing to {group.chat_id}: {e}")
                    
    except Exception as e:
        logger.error(f"Scheduler DB error: {e}")

def start_scheduler(app: Application):
    """Attaches the daily jobs to the bot's job queue."""
    # 🚨 FIX: Force the scheduler to use IST regardless of the server's timezone
    ist_tz = ZoneInfo("Asia/Kolkata")
    target_time = datetime.time(hour=7, minute=0, second=0, tzinfo=ist_tz)
    
    # Schedule the job
    app.job_queue.run_daily(
        morning_briefing,
        time=target_time,
        name="7AM_Morning_Briefing"
    )
    
    logger.info(f"⏰ Scheduler initialized: Morning briefing locked for {target_time} IST.")