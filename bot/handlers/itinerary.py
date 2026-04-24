from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, delete
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, Landmark, TripPlan
from bot.handlers.logistics import calculate_distance

# --- GET Logic (Universal) ---

async def explore_nearby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finds the nearest landmark FROM THE DATABASE for this specific chat."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    async with AsyncSessionLocal() as session:
        # 1. Get user location
        user_res = await session.execute(select(UserLocation).where(UserLocation.telegram_id == user_id))
        user_loc = user_res.scalar_one_or_none()
        
        # 2. Get all landmarks for THIS group
        land_res = await session.execute(select(Landmark).where(Landmark.chat_id == chat_id))
        db_landmarks = land_res.scalars().all()

    if not user_loc or not db_landmarks:
        await update.message.reply_text("📍 Either I don't have your location or no landmarks are set for this trip!")
        return

    closest, min_dist = None, float('inf')
    for lm in db_landmarks:
        dist = calculate_distance(user_loc.latitude, user_loc.longitude, lm.lat, lm.lon)
        if dist < min_dist:
            min_dist, closest = dist, lm

    if min_dist > 50000:
        await update.message.reply_text(f"📍 You are {min_dist/1000:.1f}km away from the nearest landmark!")
        return

    msg = (
        f"🧭 <b>Near {closest.name}</b>\n"
        f"🏨 <b>Stay:</b> {closest.stay_info}\n"
        f"🍲 <b>Food:</b> {closest.food_info}\n"
        f"🔭 <b>Sight:</b> {closest.sight_info}"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the custom plan stored for this group."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(TripPlan).where(TripPlan.chat_id == update.message.chat_id))
        plan = res.scalar_one_or_none()
    
    if not plan:
        await update.message.reply_text("📝 No plan set! Use /set_plan to create one.")
        return
    await update.message.reply_text(f"🏔️ <b>Trip Itinerary</b>\n\n{plan.plan_text}", parse_mode='HTML')


# --- SET Logic (Universal) ---

async def add_landmark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /add_landmark name | lat,lon | stay | food | sight"""
    try:
        parts = " ".join(context.args).split("|")
        name = parts[0].strip()
        coords = parts[1].strip().split(",")
        lat, lon = float(coords[0]), float(coords[1])
        
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(Landmark(
                    chat_id=update.message.chat_id, name=name, lat=lat, lon=lon,
                    stay_info=parts[2].strip(), food_info=parts[3].strip(), sight_info=parts[4].strip()
                ))
        await update.message.reply_text(f"✅ Landmark <b>{name}</b> added!", parse_mode='HTML')
    except:
        await update.message.reply_text("⚠️ Use: /add_landmark Name | Lat,Lon | Stay | Food | Sight")

async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /set_plan <multiline text>"""
    plan_content = " ".join(context.args)
    if not plan_content: return
    
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Delete old plan, add new
            await session.execute(delete(TripPlan).where(TripPlan.chat_id == update.message.chat_id))
            session.add(TripPlan(chat_id=update.message.chat_id, plan_text=plan_content))
    await update.message.reply_text("✅ Trip plan updated!")


# --- PLACEHOLDERS FOR SERVER.PY IMPORTS ---

async def trip_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Placeholder for the AI Gallery feature."""
    await update.message.reply_text("📸 AI Trip Gallery feature is currently under construction!")

async def set_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Placeholder for linking the gallery."""
    await update.message.reply_text("🔗 Gallery linking feature is coming soon!")

async def sos_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Placeholder for the SOS Safety feature."""
    await update.message.reply_text("🚨 SOS Protocol activated! (Broadcasting logic coming soon...)")