import math
import os
import requests
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.dialects.postgresql import insert as pg_insert # 🛠️ Correct PostgreSQL Dialect
from sqlalchemy import select, func
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, GroupMember, User
from bot.utils.logger import setup_logger
from datetime import timedelta, datetime

logger = setup_logger("LogisticsHandler")

# --- Helper Logic ---
def calculate_distance(lat1, lon1, lat2, lon2):
    """Haversine formula to calculate distance in meters."""
    R = 6371000 # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# --- Handlers ---

async def track_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves user's location with FK guards and session flushing."""
    
    msg = update.message or update.edited_message
    if not msg or not msg.location:
        return

    if msg.chat.type == 'private':
        await msg.reply_text("⚠️ Please share your location inside the Trip Group!")
        return

    user = msg.from_user
    loc = msg.location
    chat_id = msg.chat_id

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. 🛡️ FK GUARD: Register User if missing
            await session.execute(pg_insert(User).values(
                telegram_id=user.id,
                name=user.full_name,
                username=user.username
            ).on_conflict_do_nothing(index_elements=['telegram_id']))

            # 2. 🛡️ FK GUARD: Register Trip Group if missing
            await session.execute(pg_insert(TripGroup).values(
                chat_id=chat_id,
                trip_name=msg.chat.title or "New Expedition"
            ).on_conflict_do_nothing(index_elements=['chat_id']))

            # 🛠️ THE FIX: Flush parent rows to DB buffer so child rows (Location/Member) can find them
            await session.flush()

            # 3. Upsert Location
            loc_stmt = pg_insert(UserLocation).values(
                telegram_id=user.id,
                name=user.first_name,
                latitude=loc.latitude,
                longitude=loc.longitude,
                updated_at=datetime.utcnow()
            ).on_conflict_do_update(
                index_elements=['telegram_id'],
                set_={
                    'latitude': loc.latitude, 
                    'longitude': loc.longitude, 
                    'updated_at': datetime.utcnow()
                }
            )
            await session.execute(loc_stmt)

            # 4. Register Membership
            member_stmt = pg_insert(GroupMember).values(
                chat_id=chat_id,
                user_id=user.id
            ).on_conflict_do_nothing(index_elements=['chat_id', 'user_id'])
            await session.execute(member_stmt)

    if update.message:
        await update.message.reply_text(
            f"📍 <b>{user.first_name}</b>: Check-in saved! 🏔️", 
            parse_mode='HTML'
        )

async def where_is_everyone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the last seen location of squad members assigned to THIS chat."""
    chat_id = update.message.chat_id
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserLocation)
            .join(GroupMember, UserLocation.telegram_id == GroupMember.user_id)
            .where(GroupMember.chat_id == chat_id)
            .order_by(UserLocation.updated_at.desc())
        )
        locations = result.scalars().all()

    if not locations:
        await update.message.reply_text("📍 No location pins found for this squad!")
        return

    msg = "<b>📍 Squad Status (Last Seen IST)</b>\n➖➖➖➖➖➖➖➖➖➖\n"
    
    for loc in locations:
        ist_time = loc.updated_at + timedelta(hours=5, minutes=30)
        time_str = ist_time.strftime("%I:%M %p")
        maps_url = f"https://www.google.com/maps?q={loc.latitude},{loc.longitude}"
        
        msg += f"👤 <b>{loc.name}</b>\n🕒 {time_str}\n📍 <a href='{maps_url}'>Track on Map</a>\n\n"
    
    await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)

async def plan_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: /plan_trip <lat,lon> <name>")
        return
    try:
        coords = context.args[0].split(",")
        lat, lon = float(coords[0]), float(coords[1])
        dest_name = " ".join(context.args[1:])
        async with AsyncSessionLocal() as session:
            async with session.begin():
                group = await session.get(TripGroup, update.message.chat_id)
                if not group:
                    group = TripGroup(chat_id=update.message.chat_id)
                    session.add(group)
                group.dest_lat, group.dest_lon, group.destination_name = lat, lon, dest_name
                group.trip_name = f"{dest_name} Trip"
        await update.message.reply_text(f"✅ Trip set to <b>{dest_name}</b>!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Plan Trip Error: {e}")
        await update.message.reply_text("⚠️ Error in coordinates format.")

async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    async with AsyncSessionLocal() as session:
        group = await session.get(TripGroup, chat_id)
    
    if not group or not group.dest_lat:
        await update.message.reply_text("⚠️ Use /plan_trip first.")
        return
        
    api_key = os.getenv("WEATHER_API_KEY")
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={group.dest_lat}&lon={group.dest_lon}&appid={api_key}&units=metric"
    try:
        data = requests.get(url).json()
        temp = data['main']['temp']
        wind = data['wind']['speed']
        desc = data['weather'][0]['description'].capitalize()
        
        drone_safety = "✅ Safe" if wind < 5 else "❌ High Wind"
        
        await update.message.reply_text(
            f"🌤️ <b>Weather: {group.destination_name}</b>\n"
            f"🌡️ {temp}°C | 💨 {wind}m/s\n"
            f"☁️ {desc}\n"
            f"🚁 <b>Drone:</b> {drone_safety}", 
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Weather Error: {e}")
        await update.message.reply_text("⚠️ Weather service currently down.")