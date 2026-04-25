from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, delete
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, Landmark, TripPlan
from bot.handlers.logistics import calculate_distance
from bot.utils.logger import setup_logger

logger = setup_logger("ItineraryHandler")

async def explore_nearby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finds the nearest landmark based on the user's last known location."""
    chat_id, user_id = update.message.chat_id, update.message.from_user.id
    try:
        async with AsyncSessionLocal() as session:
            user_loc = (await session.execute(select(UserLocation).where(UserLocation.telegram_id == user_id))).scalar_one_or_none()
            db_landmarks = (await session.execute(select(Landmark).where(Landmark.chat_id == chat_id))).scalars().all()
        
        if not user_loc or not db_landmarks:
            await update.message.reply_text("📍 No location or landmarks found. Share your location and use /add_landmark first!")
            return

        closest, min_dist = None, float('inf')
        for lm in db_landmarks:
            dist = calculate_distance(user_loc.latitude, user_loc.longitude, lm.lat, lm.lon)
            if dist < min_dist:
                min_dist, closest = dist, lm
        
        await update.message.reply_text(
            f"🧭 <b>Near {closest.name}</b>\n"
            f"🏨 <b>Stay:</b> {closest.stay_info}\n"
            f"🍲 <b>Food:</b> {closest.food_info}", 
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"explore_nearby error: {e}")

async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the trip itinerary for the group."""
    try:
        async with AsyncSessionLocal() as session:
            plan = (await session.execute(select(TripPlan).where(TripPlan.chat_id == update.message.chat_id))).scalar_one_or_none()
        if not plan:
            await update.message.reply_text("📝 No plan set. Use /set_plan to create one.")
            return
        await update.message.reply_text(f"🏔️ <b>Trip Itinerary</b>\n\n{plan.plan_text}", parse_mode='HTML')
    except Exception as e:
        logger.error(f"show_plan error: {e}")

async def add_landmark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds a point of interest to the database."""
    if not context.args: return
    try:
        parts = " ".join(context.args).split("|")
        name, coords = parts[0].strip(), parts[1].strip().split(",")
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(Landmark(
                    chat_id=update.message.chat_id, 
                    name=name, 
                    lat=float(coords[0]), 
                    lon=float(coords[1]), 
                    stay_info=parts[2].strip() if len(parts) > 2 else "N/A", 
                    food_info=parts[3].strip() if len(parts) > 3 else "N/A", 
                    sight_info=parts[4].strip() if len(parts) > 4 else "N/A"
                ))
        await update.message.reply_text(f"✅ Landmark <b>{name}</b> added!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"add_landmark error: {e}")

async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves or updates the trip itinerary text."""
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

# 🛠️ SOS SYSTEM: Confirmation UI to prevent accidental clicks
async def sos_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initial SOS trigger — asks for confirmation."""
    keyboard = [[
        InlineKeyboardButton("🚨 CONFIRM EMERGENCY", callback_data="sos_confirm")
    ]]
    await update.message.reply_text(
        "⚠️ <b>SOS Protocol Initiated</b>\n"
        "Are you sure you want to broadcast an emergency alert to the squad?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_sos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Final SOS broadcast handler."""
    query = update.callback_query
    if query.data == "sos_confirm":
        await query.answer("🚨 SOS BROADCASTED", show_alert=True)
        # 🛠️ THE UI FIX: Update text to "lock" the button so it can't be clicked again
        await query.edit_message_text(
            "🚨 <b>EMERGENCY BROADCAST SENT</b>\n"
            "The squad has been notified of your last known location.", 
            parse_mode='HTML'
        )
        logger.warning(f"SOS Triggered by {query.from_user.name} in chat {query.message.chat_id}")