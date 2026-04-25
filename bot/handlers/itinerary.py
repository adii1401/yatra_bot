from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, delete
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, Landmark, TripPlan
from bot.handlers.logistics import calculate_distance
from bot.utils.logger import setup_logger

logger = setup_logger("ItineraryHandler")

async def explore_nearby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, user_id = update.message.chat_id, update.message.from_user.id
    try:
        async with AsyncSessionLocal() as session:
            user_loc = (await session.execute(select(UserLocation).where(UserLocation.telegram_id == user_id))).scalar_one_or_none()
            db_landmarks = (await session.execute(select(Landmark).where(Landmark.chat_id == chat_id))).scalars().all()
        if not user_loc or not db_landmarks:
            await update.message.reply_text("📍 No location or landmarks found.")
            return
        closest, min_dist = None, float('inf')
        for lm in db_landmarks:
            dist = calculate_distance(user_loc.latitude, user_loc.longitude, lm.lat, lm.lon)
            if dist < min_dist: min_dist, closest = dist, lm
        await update.message.reply_text(f"🧭 <b>Near {closest.name}</b>\n🏨 {closest.stay_info}\n🍲 {closest.food_info}", parse_mode='HTML')
    except Exception as e:
        logger.error(f"explore_nearby error: {e}")

async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with AsyncSessionLocal() as session:
            plan = (await session.execute(select(TripPlan).where(TripPlan.chat_id == update.message.chat_id))).scalar_one_or_none()
        if not plan:
            await update.message.reply_text("📝 No plan set.")
            return
        await update.message.reply_text(f"🏔️ <b>Itinerary</b>\n\n{plan.plan_text}", parse_mode='HTML')
    except Exception as e:
        logger.error(f"show_plan error: {e}")

async def add_landmark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    try:
        parts = " ".join(context.args).split("|")
        name, coords = parts[0].strip(), parts[1].strip().split(",")
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(Landmark(chat_id=update.message.chat_id, name=name, lat=float(coords[0]), lon=float(coords[1]), stay_info=parts[2].strip(), food_info=parts[3].strip(), sight_info=parts[4].strip()))
        await update.message.reply_text(f"✅ Landmark <b>{name}</b> added!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"add_landmark error: {e}")

async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    plan_content = " ".join(context.args)
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(TripPlan).where(TripPlan.chat_id == update.message.chat_id))
                session.add(TripPlan(chat_id=update.message.chat_id, plan_text=plan_content))
        await update.message.reply_text("✅ Plan updated!")
    except Exception as e:
        logger.error(f"set_plan error: {e}")

# 🛠️ RESTORED: To fix the server.py import error
async def sos_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚨 <b>SOS Feature</b>: Currently under construction, please contact emergency services directly.", parse_mode='HTML')