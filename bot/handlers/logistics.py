import math
import os
import httpx
import asyncio
import time
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select, func
from bot.database.db_config import get_safe_session, UserLocation, TripGroup, GroupMember, User
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
LAST_LOCATION_PING={}
async def track_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves user coordinates and ensures they are linked to the current group."""
    msg = update.message or update.edited_message
    if not msg or not msg.location:
        return

    if msg.chat.type == 'private':
        return

    chat_id, user = msg.chat_id, msg.from_user
    lat, lon = msg.location.latitude, msg.location.longitude
    current_time = time.time()
    last_ping =LAST_LOCATION_PING.get(user.id,0)

    if current_time - last_ping < 60:
        return
    LAST_LOCATION_PING[user.id] = current_time

    try:
        async with get_safe_session() as session:
            # ✅ Transaction block for writes prevents connection state errors
            async with session.begin():
                await session.execute(
                    pg_insert(User)
                    .values(telegram_id=user.id, name=user.full_name)
                    .on_conflict_do_nothing(index_elements=['telegram_id'])
                )
                await session.execute(
                    pg_insert(TripGroup)
                    .values(chat_id=chat_id, trip_name=msg.chat.title)
                    .on_conflict_do_nothing(index_elements=['chat_id'])
                )
                await session.execute(
                    pg_insert(GroupMember)
                    .values(chat_id=chat_id, user_id=user.id)
                    .on_conflict_do_nothing(index_elements=['chat_id', 'user_id'])
                )
                await session.execute(
                    pg_insert(UserLocation)
                    .values(telegram_id=user.id, latitude=lat, longitude=lon)
                    .on_conflict_do_update(
                        index_elements=['telegram_id'],
                        set_={'latitude': lat, 'longitude': lon, 'updated_at': func.now()}
                    )
                )
        await update.message.reply_text(f"📍 <b>Location Locked, {user.first_name}!</b>\nYou are now on the squad map.", parse_mode='HTML')
    except Exception as e:
        logger.error(f"track_location error: {e}")

async def plan_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the trip destination using Nominatim search."""
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /plan_trip [destination name]")
        return

    search_query = "+".join(context.args)
    status_msg = await update.message.reply_text("🔍 Searching destination...")

    try:
        # 🚨 FIX: Unique User-Agent to bypass OpenStreetMap cloud provider blocks
        headers = {"User-Agent": "Yatra_Bot_Kedarnath_Expedition/2.0 (contact: @adi1401)"}
        url = f"https://nominatim.openstreetmap.org/search?q={search_query}&format=json&limit=1"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.error(f"Nominatim Error {resp.status_code}: {resp.text}")
                await status_msg.edit_text("⚠️ Map service is busy. Please try again in a minute.")
                return
            
            data = resp.json()

        if not data:
            await status_msg.edit_text("📍 Destination not found. Try a broader name.")
            return

        name = data[0]['display_name'].split(",")[0]
        lat, lon = float(data[0]['lat']), float(data[0]['lon'])
        chat_id = update.message.chat_id

        async with get_safe_session() as session:
            # ✅ Transaction block for destination update
            async with session.begin():
                await session.execute(
                    pg_insert(TripGroup)
                    .values(chat_id=chat_id, destination_name=name, dest_lat=lat, dest_lon=lon)
                    .on_conflict_do_update(
                        index_elements=['chat_id'],
                        set_={'destination_name': name, 'dest_lat': lat, 'dest_lon': lon}
                    )
                )

        await status_msg.edit_text(f"✅ Trip destination set to: <b>{name}</b>", parse_mode='HTML')

    except Exception as e:
        logger.error(f"plan_trip error: {e}")
        await status_msg.edit_text("⚠️ Search timed out or failed. Please try again.")

async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches destination weather with Drone safety check."""
    chat_id = update.message.chat_id
    try:
        async with get_safe_session() as session:
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
        
        # Drone safety threshold logic
        drone = "✅ Flyable" if wind < 5 else ("⚠️ Marginal" if wind < 10 else "❌ Too windy")

        await update.message.reply_text(
            f"🌤️ <b>Weather: {group.destination_name}</b>\n➖➖➖➖➖➖➖➖➖➖\n"
            f"🌡️ Temp: <b>{temp}°C</b>\n"
            f"☁️ Sky: <b>{desc}</b>\n"
            f"💨 Wind: <b>{wind} m/s</b>\n"
            f"🚁 Drone: <b>{drone}</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"weather error: {e}")
        await update.message.reply_text("⚠️ Failed to fetch weather. Check API key.")

async def where_is_everyone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows clickable map links for all group members."""
    chat_id = update.message.chat_id
    
    try:
        async with get_safe_session() as session:
            results = await session.execute(
                select(User.name, UserLocation.latitude, UserLocation.longitude, UserLocation.updated_at)
                .join(UserLocation, User.telegram_id == UserLocation.telegram_id)
                .join(GroupMember, User.telegram_id == GroupMember.user_id)
                .where(GroupMember.chat_id == chat_id)
            )
            locations = results.all()

        if not locations:
            await update.message.reply_text("📍 No live locations found. Share your 'Live Location' in this group!")
            return

        msg = "🗺️ <b>Squad Live Locations</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        
        for name, lat, lon, updated in locations:
            time_str = format_ist(updated)
            
            # ✅ FIX: Generates a direct, clickable Google Maps pin!
            maps_url = f"https://www.google.com/maps?q={lat},{lon}"

            msg += f"👤 <b>{name}</b>\n   📍 <a href='{maps_url}'>View on Map</a>\n   🕒 {time_str}\n\n"

        await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"whereis error: {e}")
        await update.message.reply_text("⚠️ Error fetching locations.")