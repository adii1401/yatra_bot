import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ContextTypes
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from bot.database.db_config import get_safe_session, UserLocation, TripGroup, Landmark, TripPlan, TripDocument, User, PackingItem
from bot.handlers.logistics import calculate_distance
from bot.utils.logger import setup_logger
from telegram.error import BadRequest

logger = setup_logger("ItineraryHandler")


async def add_packing_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not context.args:
        await update.message.reply_text("⚠️ Usage: <code>/add_item First Aid Kit</code>", parse_mode='HTML')
        return

    item_name = " ".join(context.args)
    try:
        async with get_safe_session() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(TripGroup)
                    .values(chat_id=chat_id, trip_name=update.message.chat.title)
                    .on_conflict_do_nothing(index_elements=['chat_id'])
                )
                new_item = PackingItem(chat_id=chat_id, item_name=item_name)
                session.add(new_item)
        await update.message.reply_text(f"✅ Added <b>{item_name}</b> to the squad packing list.\nUse /packing to check off items.", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Packing Item Error: {e}")
        await update.message.reply_text("⚠️ Failed to add item.")


async def packing_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    async with get_safe_session() as session:
        items = (await session.execute(select(PackingItem).where(PackingItem.chat_id == chat_id))).scalars().all()

    if not items:
        await update.message.reply_text("🎒 Packing list is empty. Add items using <code>/add_item [name]</code>", parse_mode='HTML')
        return

    keyboard = []
    for i in items:
        status = "✅" if i.is_checked else "⬜"
        label = f"{status} {i.item_name}"
        if i.is_checked and i.checked_by:
            label += f" ({i.checked_by})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"pack_{i.id}")])

    await update.message.reply_text(
        "🎒 <b>Squad Packing Checklist</b>\nTap to claim an item:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def handle_packing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        return

    item_id = int(query.data.split("_")[1])
    user_name = query.from_user.first_name
    chat_id = query.message.chat.id

    try:
        # ✅ get_safe_session() handles DB wake-up internally; no manual retry loop needed.
        async with get_safe_session() as session:
            async with session.begin():
                item = await session.get(PackingItem, item_id)
                if not item:
                    return

                if item.is_checked:
                    item.is_checked = False
                    item.checked_by = None
                else:
                    item.is_checked = True
                    item.checked_by = user_name

            items = (await session.execute(select(PackingItem).where(PackingItem.chat_id == chat_id))).scalars().all()

        keyboard = []
        for i in items:
            status = "✅" if i.is_checked else "⬜"
            label = f"{status} {i.item_name}"
            if i.is_checked and i.checked_by:
                label += f" ({i.checked_by})"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"pack_{i.id}")])

        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.error(f"Keyboard edit error: {e}")

    except Exception as e:
        logger.error(f"Packing callback error: {e}")


async def add_landmark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.edited_message
    if not msg.reply_to_message or not msg.reply_to_message.location:
        await msg.reply_text("⚠️ You must REPLY to a location pin with /add_landmark [name]")
        return

    if not context.args:
        await msg.reply_text("⚠️ Please provide a name. Ex: /add_landmark Secret Waterfall")
        return

    name = " ".join(context.args)
    lat = msg.reply_to_message.location.latitude
    lon = msg.reply_to_message.location.longitude
    chat_id = msg.chat_id

    try:
        async with get_safe_session() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(TripGroup)
                    .values(chat_id=chat_id, trip_name=msg.chat.title)
                    .on_conflict_do_nothing(index_elements=['chat_id'])
                )
                session.add(Landmark(chat_id=chat_id, name=name, latitude=lat, longitude=lon))
        await msg.reply_text(f"✅ Landmark saved: <b>{name}</b>", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Landmark error: {e}")
        await msg.reply_text("⚠️ Failed to save landmark.")


async def explore_nearby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    try:
        async with get_safe_session() as session:
            loc = (await session.execute(select(UserLocation).where(UserLocation.telegram_id == user_id))).scalar_one_or_none()
            if not loc:
                await update.message.reply_text("📍 Please share your live location first!")
                return
            landmarks = (await session.execute(select(Landmark).where(Landmark.chat_id == chat_id))).scalars().all()

        if not landmarks:
            await update.message.reply_text("🗺️ No landmarks added yet. Reply to a location pin with /add_landmark.")
            return

        msg = "🗺️ <b>Nearby Landmarks</b>\n\n"
        for lm in landmarks:
            dist = calculate_distance(loc.latitude, loc.longitude, lm.latitude, lm.longitude)
            if dist < 10000:
                msg += f"📍 <b>{lm.name}</b> - {dist/1000:.1f} km away\n"

        if msg == "🗺️ <b>Nearby Landmarks</b>\n\n":
            msg = "🗺️ No landmarks found within 10km."

        await update.message.reply_text(msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"explore error: {e}")


async def sos_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user = update.message.from_user

    async with get_safe_session() as session:
        loc = (await session.execute(
            select(UserLocation).where(UserLocation.telegram_id == user.id)
        )).scalar_one_or_none()

    keyboard = [[InlineKeyboardButton("✅ Mark as Safe / Resolve", callback_data=f"sos_{chat_id}")]]

    msg = f"🚨 <b>SOS ALERT TRIGGERED</b> 🚨\n\n<b>{user.first_name}</b> has signaled an emergency!\n"

    if loc:
        maps_url = f"https://www.google.com/maps?q={loc.latitude},{loc.longitude}"
        msg += f"📍 Last Known Location: <a href='{maps_url}'>Open on Maps</a>\n"
    else:
        msg += "⚠️ <i>No GPS coordinates found for this user.</i>\n"

    msg += "\nAll squad members please check in immediately."

    await update.message.reply_text(
        msg,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML',
        disable_web_page_preview=True
    )


async def handle_sos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    resolver = query.from_user.first_name
    await query.edit_message_text(f"✅ <b>SOS Resolved</b>\nCleared by {resolver}. Status normal.", parse_mode='HTML')


# Placeholders for gallery/plan features
async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE): pass
async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE): pass
async def trip_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE): pass
async def set_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE): pass