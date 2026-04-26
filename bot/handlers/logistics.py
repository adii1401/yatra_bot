import math
import os
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, GroupMember, User
from bot.utils.logger import setup_logger
from bot.utils.helpers import format_ist
from datetime import datetime

logger = setup_logger("LogisticsHandler")

def calculate_distance(lat1, lon1, lat2, lon2) -> float:
    """Haversine formula — returns distance in meters."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

async def track_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.edited_message
    if not msg or not msg.location: return
    if msg.chat.type == 'private':
        await msg.reply_text("⚠️ Please share your location inside the trip group!")
        return

    user, loc, chat_id = msg.from_user, msg.location, msg.chat_id

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Ensure User and Group exist first
                await session.execute(pg_insert(User).values(telegram_id=user.id, name=user.full_name, username=user.username).on_conflict_do_nothing(index_elements=['telegram_id']))
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id, trip_name=msg.chat.title or "Trip Group").on_conflict_do_nothing(index_elements=['chat_id']))
                await session.flush()

                # 🛠️ FIX: Removed 'name' column from UserLocation to match schema
                await session.execute(
                    pg_insert(UserLocation).values(
                        telegram_id=user.id, latitude=loc.latitude, longitude=loc.longitude, updated_at=datetime.utcnow()
                    ).on_conflict_do_update(
                        index_elements=['telegram_id'],
                        set_={'latitude': loc.latitude, 'longitude': loc.longitude, 'updated_at': datetime.utcnow()}
                    )
                )

                # 🛠️ FIX: Use specific constraint name for group members
                await session.execute(
                    pg_insert(GroupMember).values(chat_id=chat_id, user_id=user.id)
                    .on_conflict_do_nothing(index_elements=['chat_id', 'user_id'])
                )

        if update.message:
            await update.message.reply_text(f"📍 <b>{user.first_name}</b> checked in!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"track_location error: {e}")
        if update.message:
            await update.message.reply_text("⚠️ Could not save your location.")

async def where_is_everyone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            # 🛠️ FIX: Joins User table to get the name safely since it's not in UserLocation anymore
            result = await session.execute(
                select(UserLocation, User.name)
                .join(User, UserLocation.telegram_id == User.telegram_id)
                .join(GroupMember, UserLocation.telegram_id == GroupMember.user_id)
                .where(GroupMember.chat_id == chat_id)
                .order_by(UserLocation.updated_at.desc())
            )
            locations = result.all()

        if not locations:
            await update.message.reply_text("📍 No locations found for this squad yet.")
            return

        msg = "📍 <b>Squad Status</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        for loc, user_name in locations:
            time_str = format_ist(loc.updated_at)
            msg += f"👤 <b>{user_name}</b>\n🕒 Last seen: {time_str}\n📍 <a href='http://maps.google.com/?q={loc.latitude},{loc.longitude}'>Open on Maps</a>\n\n"

        await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"whereis error: {e}")

async def plan_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    🛠️ UPGRADE: Automatically finds coordinates using OpenStreetMap API.
    Usage: /plan_trip Kedarnath
    """
    chat_id = update.message.chat_id
    if not context.args:
        await update.message.reply_text("⚠️ Usage: <code>/plan_trip Kedarnath</code>", parse_mode='HTML')
        return

    search_query = " ".join(context.args)
    status_msg = await update.message.reply_text(f"🔍 Searching for <b>{search_query}</b>...", parse_mode='HTML')

    try:
        # 🛠️ SMART SEARCH: Find lat/lon by name
        url = f"https://nominatim.openstreetmap.org/search?q={search_query}&format=json&limit=1"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "TripOS-Bot/1.0"})
            data = resp.json()

        if not data:
            await status_msg.edit_text(f"❌ Could not find '{search_query}'.")
            return

        lat, lon = float(data[0]['lat']), float(data[0]['lon'])
        dest_name = data[0]['display_name'].split(",")[0]

        async with AsyncSessionLocal() as session:
            async with session.begin():
                group = await session.get(TripGroup, chat_id)
                if not group:
                    group = TripGroup(chat_id=chat_id)
                    session.add(group)
                # 🛠️ FIX: Update re-added coordinate columns
                group.dest_lat, group.dest_lon = lat, lon
                group.destination_name = dest_name
                group.trip_name = f"{dest_name} Trip"

        await status_msg.edit_text(
            f"✅ Destination locked: <b>{dest_name}</b>\n📍 <a href='http://maps.google.com/?q={lat},{lon}'>View on Maps</a>",
            parse_mode='HTML', disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"plan_trip error: {e}")
        await status_msg.edit_text("⚠️ Search failed. Try again later.")

async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            group = await session.get(TripGroup, chat_id)

        if not group or not group.dest_lat:
            await update.message.reply_text("⚠️ Set destination first with /plan_trip.")
            return

        api_key = os.getenv("WEATHER_API_KEY")
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={group.dest_lat}&lon={group.dest_lon}&appid={api_key}&units=metric"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            data = resp.json()

        temp = data['main']['temp']
        wind = data['wind']['speed']
        desc = data['weather'][0]['description'].capitalize()
        drone = "✅ Flyable" if wind < 5 else ("⚠️ Marginal" if wind < 10 else "❌ Too windy")

        await update.message.reply_text(
            f"🌤️ <b>Weather: {group.destination_name}</b>\n➖➖➖➖➖➖➖➖➖➖\n🌡️ Temp: {temp}°C\n💨 Wind: {wind} m/s\n☁️ Conditions: {desc}\n🚁 Drone: {drone}",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"weather error: {e}")
        await update.message.reply_text("⚠️ Could not fetch weather.")