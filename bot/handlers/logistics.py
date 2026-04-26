import math
import os
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, GroupMember, User
from bot.utils.logger import setup_logger
from bot.utils.helpers import parse_coordinate, format_ist
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
    """
    Triggered when a user shares their location in the group.
    Saves/updates their coordinates and registers them to this group.
    """
    msg = update.message or update.edited_message
    if not msg or not msg.location:
        return

    if msg.chat.type == 'private':
        await msg.reply_text(
            "⚠️ Please share your location inside the trip group, not here in DM."
        )
        return

    user = msg.from_user
    loc = msg.location
    chat_id = msg.chat_id

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # FK guards — ensure parent rows exist
                await session.execute(
                    pg_insert(User)
                    .values(telegram_id=user.id, name=user.full_name, username=user.username)
                    .on_conflict_do_nothing(index_elements=['telegram_id'])
                )
                await session.execute(
                    pg_insert(TripGroup)
                    .values(chat_id=chat_id, trip_name=msg.chat.title or "Trip Group")
                    .on_conflict_do_nothing(index_elements=['chat_id'])
                )
                await session.flush()

                # Upsert location
                await session.execute(
                    pg_insert(UserLocation)
                    .values(
                        telegram_id=user.id,
                        name=user.first_name,
                        latitude=loc.latitude,
                        longitude=loc.longitude,
                        updated_at=datetime.utcnow()
                    )
                    .on_conflict_do_update(
                        index_elements=['telegram_id'],
                        set_={
                            'latitude': loc.latitude,
                            'longitude': loc.longitude,
                            'updated_at': datetime.utcnow()
                        }
                    )
                )

                # Register group membership
                await session.execute(
                    pg_insert(GroupMember)
                    .values(chat_id=chat_id, user_id=user.id)
                    .on_conflict_do_nothing(index_elements=['chat_id', 'user_id'])
                )

        if update.message:
            await update.message.reply_text(
                f"📍 <b>{user.first_name}</b> checked in! Squad can now see your location. 🏔️",
                parse_mode='HTML'
            )
    except Exception as e:
        logger.error(f"track_location error (user {user.id}, chat {chat_id}): {e}")
        if update.message:
            await update.message.reply_text(
                "⚠️ Could not save your location. Try sharing again."
            )


async def where_is_everyone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /whereis
    Shows last known location of all squad members in this group.
    """
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserLocation)
                .join(GroupMember, UserLocation.telegram_id == GroupMember.user_id)
                .where(GroupMember.chat_id == chat_id)
                .order_by(UserLocation.updated_at.desc())
            )
            locations = result.scalars().all()

        if not locations:
            await update.message.reply_text(
                "📍 No locations found for this squad yet.\n"
                "Everyone needs to share their location in the group first!"
            )
            return

        msg = "📍 <b>Squad Status</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        for loc in locations:
            time_str = format_ist(loc.updated_at)
            maps_url = f"https://www.google.com/maps?q={loc.latitude},{loc.longitude}"
            msg += (
                f"👤 <b>{loc.name}</b>\n"
                f"🕒 Last seen: {time_str}\n"
                f"📍 <a href='{maps_url}'>Open on Maps</a>\n\n"
            )

        await update.message.reply_text(
            msg,
            parse_mode='HTML',
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"where_is_everyone error (chat {chat_id}): {e}")
        await update.message.reply_text(
            "⚠️ Could not fetch locations right now. Try again in a moment."
        )


async def plan_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /plan_trip <lat,lon> <destination name>
    Accepts both decimal and DMS coordinate formats.
    """
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ <b>How to set destination:</b>\n"
            "<code>/plan_trip 30.7346,79.0669 Kedarnath</code>\n\n"
            "💡 Get coordinates: Open Google Maps → Long press on location → Copy coordinates",
            parse_mode='HTML'
        )
        return

    try:
        raw_coords = context.args[0].split(",")
        if len(raw_coords) != 2:
            raise ValueError("Need exactly lat,lon")
        lat = parse_coordinate(raw_coords[0])
        lon = parse_coordinate(raw_coords[1])
        dest_name = " ".join(context.args[1:])

        async with AsyncSessionLocal() as session:
            async with session.begin():
                group = await session.get(TripGroup, update.message.chat_id)
                if not group:
                    group = TripGroup(chat_id=update.message.chat_id)
                    session.add(group)
                group.dest_lat = lat
                group.dest_lon = lon
                group.destination_name = dest_name
                group.trip_name = f"{dest_name} Trip"

        maps_url = f"https://www.google.com/maps?q={lat},{lon}"
        await update.message.reply_text(
            f"✅ Destination set to <b>{dest_name}</b>!\n"
            f"📍 <a href='{maps_url}'>View on Maps</a>\n\n"
            f"Use /weather to check conditions there.",
            parse_mode='HTML',
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"plan_trip error: {e}")
        await update.message.reply_text(
            "⚠️ Could not parse coordinates.\n"
            "Use decimal format: <code>/plan_trip 30.7346,79.0669 Kedarnath</code>\n"
            "Get coordinates from Google Maps (long press on the location).",
            parse_mode='HTML'
        )


async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /weather
    Fetches weather at the trip destination using async HTTP.
    """
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            group = await session.get(TripGroup, chat_id)

        if not group or not group.dest_lat:
            await update.message.reply_text(
                "⚠️ No destination set yet.\n"
                "Use /plan_trip to set one first."
            )
            return

        api_key = os.getenv("WEATHER_API_KEY")
        if not api_key:
            await update.message.reply_text("⚠️ Weather API not configured.")
            return

        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={group.dest_lat}&lon={group.dest_lon}"
            f"&appid={api_key}&units=metric"
        )

        # Use async HTTP — does not block the event loop
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        temp = data['main']['temp']
        feels = data['main']['feels_like']
        humidity = data['main']['humidity']
        wind = data['wind']['speed']
        desc = data['weather'][0]['description'].capitalize()
        drone = "✅ Flyable" if wind < 5 else ("⚠️ Marginal" if wind < 10 else "❌ Too windy")

        await update.message.reply_text(
            f"🌤️ <b>Weather: {group.destination_name}</b>\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"🌡️ Temp: {temp}°C (feels {feels}°C)\n"
            f"💧 Humidity: {humidity}%\n"
            f"💨 Wind: {wind} m/s\n"
            f"☁️ Conditions: {desc}\n"
            f"🚁 Drone: {drone}",
            parse_mode='HTML'
        )

    except httpx.TimeoutException:
        await update.message.reply_text("⚠️ Weather service timed out. Try again.")
    except Exception as e:
        logger.error(f"get_weather error (chat {chat_id}): {e}")
        await update.message.reply_text("⚠️ Could not fetch weather. Try again later.")