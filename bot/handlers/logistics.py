import math
import os
import requests
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, GroupMember, User
from bot.utils.logger import setup_logger
from datetime import timedelta, datetime

logger = setup_logger("LogisticsHandler")

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

async def track_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.edited_message
    if not msg or not msg.location: return
    if msg.chat.type == 'private':
        await msg.reply_text("⚠️ Share location in the group!")
        return

    user, loc, chat_id = msg.from_user, msg.location, msg.chat_id
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(pg_insert(User).values(telegram_id=user.id, name=user.full_name, username=user.username).on_conflict_do_nothing(index_elements=['telegram_id']))
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id, trip_name=msg.chat.title or "New Trip").on_conflict_do_nothing(index_elements=['chat_id']))
                await session.flush()
                await session.execute(pg_insert(UserLocation).values(telegram_id=user.id, name=user.first_name, latitude=loc.latitude, longitude=loc.longitude, updated_at=datetime.utcnow()).on_conflict_do_update(index_elements=['telegram_id'], set_={'latitude': loc.latitude, 'longitude': loc.longitude, 'updated_at': datetime.utcnow()}))
                await session.execute(pg_insert(GroupMember).values(chat_id=chat_id, user_id=user.id).on_conflict_do_nothing(index_elements=['chat_id', 'user_id']))
        await update.message.reply_text(f"📍 <b>{user.first_name}</b>: Check-in saved!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"track_location error: {e}")

async def where_is_everyone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(UserLocation).join(GroupMember, UserLocation.telegram_id == GroupMember.user_id).where(GroupMember.chat_id == chat_id).order_by(UserLocation.updated_at.desc()))
            locations = result.scalars().all()
        if not locations:
            await update.message.reply_text("📍 No locations found.")
            return
        msg = "<b>📍 Squad Status</b>\n"
        for loc in locations:
            time_str = (loc.updated_at + timedelta(hours=5, minutes=30)).strftime("%I:%M %p")
            msg += f"👤 <b>{loc.name}</b>: {time_str} | <a href='http://google.com/maps?q={loc.latitude},{loc.longitude}'>Map</a>\n"
        await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"where_is_everyone error: {e}")

async def plan_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: /plan_trip lat,lon Name")
        return
    try:
        coords = context.args[0].split(",")
        lat, lon = float(coords[0]), float(coords[1])
        dest_name = " ".join(context.args[1:])
        async with AsyncSessionLocal() as session:
            async with session.begin():
                group = await session.get(TripGroup, update.message.chat_id)
                if not group: group = TripGroup(chat_id=update.message.chat_id); session.add(group)
                group.dest_lat, group.dest_lon, group.destination_name = lat, lon, dest_name
        await update.message.reply_text(f"✅ Trip set to <b>{dest_name}</b>!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"plan_trip error: {e}")

async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            group = await session.get(TripGroup, chat_id)
        if not group or not group.dest_lat:
            await update.message.reply_text("⚠️ Use /plan_trip first.")
            return
        data = requests.get(f"https://api.openweathermap.org/data/2.5/weather?lat={group.dest_lat}&lon={group.dest_lon}&appid={os.getenv('WEATHER_API_KEY')}&units=metric").json()
        await update.message.reply_text(f"🌤️ <b>{group.destination_name}</b>: {data['main']['temp']}°C | {data['weather'][0]['description']}", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Weather error: {e}")