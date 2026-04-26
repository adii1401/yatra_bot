from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ContextTypes
from sqlalchemy import select, delete
from bot.database.db_config import AsyncSessionLocal, UserLocation, TripGroup, Landmark, TripPlan, TripDocument, User, PackingItem
from bot.handlers.logistics import calculate_distance
from bot.utils.logger import setup_logger
from zoneinfo import ZoneInfo # 🛠️ NEW

logger = setup_logger("ItineraryHandler")

async def explore_nearby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    try:
        async with AsyncSessionLocal() as session:
            loc = (await session.execute(select(UserLocation).where(UserLocation.telegram_id == user_id))).scalar_one_or_none()
            if not loc:
                await update.message.reply_text("📍 Please share your live location first!")
                return
            landmarks = (await session.execute(select(Landmark).where(Landmark.chat_id == chat_id))).scalars().all()
        
        if not landmarks:
            await update.message.reply_text("🗺️ No landmarks added yet. Use /add_landmark.")
            return
            
        msg = "🗺️ <b>Nearby Landmarks</b>\n\n"
        for lm in landmarks:
            dist = calculate_distance(loc.latitude, loc.longitude, lm.latitude, lm.longitude)
            msg += f"📍 <b>{lm.name}</b> ({dist:.1f} km away)\n<i>{lm.notes}</i>\n\n"
        await update.message.reply_text(msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"explore error: {e}")

async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            plan = (await session.execute(select(TripPlan).where(TripPlan.chat_id == chat_id))).scalar_one_or_none()
        if plan:
            await update.message.reply_text(f"📋 <b>Expedition Plan</b>\n\n{plan.plan_text}", parse_mode='HTML')
        else:
            await update.message.reply_text("No plan set! Use <code>/set_plan Day 1: ...</code>", parse_mode='HTML')
    except Exception as e:
        logger.error(f"plan error: {e}")

async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    chat_id = update.message.chat_id
    plan_text = " ".join(context.args)
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(TripPlan).where(TripPlan.chat_id == chat_id))
                session.add(TripPlan(chat_id=chat_id, plan_text=plan_text))
        await update.message.reply_text("✅ Plan updated successfully!")
    except Exception as e:
        logger.error(f"set_plan error: {e}")

async def add_landmark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = " ".join(context.args).split("|")
        if len(args) < 3: return
        name = args[0].strip()
        coords = args[1].split(",")
        lat, lon = float(coords[0].strip()), float(coords[1].strip())
        notes = args[2].strip()
        
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(Landmark(chat_id=update.message.chat_id, name=name, latitude=lat, longitude=lon, notes=notes))
        await update.message.reply_text(f"✅ Landmark <b>{name}</b> added!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"add_landmark error: {e}")

async def trip_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            photos = (await session.execute(
                select(TripDocument)
                .where(TripDocument.chat_id == chat_id, TripDocument.file_type == "photo")
                .order_by(TripDocument.uploaded_at.desc())
                .limit(10)
            )).scalars().all()

        if not photos:
            await update.message.reply_text("📸 <b>The Gallery is empty!</b>\nUpload photos with <code>#vault</code> to see them here.", parse_mode='HTML')
            return

        media_group = [InputMediaPhoto(media=p.file_id, caption=p.caption) for p in photos]
        await context.bot.send_media_group(chat_id=chat_id, media=media_group)
    except Exception as e:
        logger.error(f"trip_gallery error: {e}")

async def set_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass # Reserved for future album naming logic

async def sos_emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🚨 CONFIRM EMERGENCY", callback_data="sos_confirm")]]
    await update.message.reply_text(
        "⚠️ <b>SOS Protocol Initiated</b>\nAre you sure you want to broadcast an emergency alert to the squad?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_sos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if query.data == "sos_confirm":
        async with AsyncSessionLocal() as session:
            loc = (await session.execute(select(UserLocation).where(UserLocation.telegram_id == user_id))).scalar_one_or_none()

        if loc:
            # 🛠️ FIX: Force Indian Standard Time
            ist_time = loc.updated_at.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%I:%M %p')
            google_maps_link = f"https://www.google.com/maps?q={loc.latitude},{loc.longitude}"
            alert_text = (
                f"🚨 <b>REAL-TIME SOS ALERT</b> 🚨\n\n"
                f"👤 <b>User:</b> {query.from_user.full_name}\n"
                f"📍 <a href='{google_maps_link}'>Live Location Pin</a>\n"
                f"⏰ <b>Last Check-in:</b> {ist_time} (IST)"
            )
        else:
            alert_text = f"🚨 <b>SOS ALERT</b>\n{query.from_user.full_name} is in trouble, but no location data is available!"

        await query.answer("🚨 SOS BROADCASTED", show_alert=True)
        await query.edit_message_text(alert_text, parse_mode='HTML', disable_web_page_preview=False)
        
        if query.message.chat.type == 'private':
             await context.bot.send_message(chat_id=chat_id, text=alert_text, parse_mode='HTML')


# 🛠️ NEW: Interactive Packing List 
async def packing_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private':
        await update.message.reply_text("⚠️ Use this command in the trip group.")
        return
        
    chat_id = update.message.chat.id
    
    async with AsyncSessionLocal() as session:
        items = (await session.execute(select(PackingItem).where(PackingItem.chat_id == chat_id))).scalars().all()
        
        # Initialize default Kedarnath gear if empty
        if not items:
            default_gear = ["Raincoat / Poncho", "Powerbank", "Medical Kit", "Trekking Shoes", "Warm Layers", "Water Bottle", "ID / Permits", "Cash"]
            async with session.begin():
                for item in default_gear:
                    session.add(PackingItem(chat_id=chat_id, item_name=item))
            items = (await session.execute(select(PackingItem).where(PackingItem.chat_id == chat_id))).scalars().all()

    keyboard = []
    for item in items:
        status = "✅" if item.is_checked else "⬜"
        label = f"{status} {item.item_name}"
        if item.is_checked and item.checked_by:
            label += f" ({item.checked_by})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"pack_{item.id}")])
        
    await update.message.reply_text(
        "🎒 <b>Squad Packing Checklist</b>\nTap to claim an item:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_packing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    item_id = int(query.data.split("_")[1])
    user_name = query.from_user.first_name
    chat_id = query.message.chat.id

    async with AsyncSessionLocal() as session:
        async with session.begin():
            item = await session.get(PackingItem, item_id)
            if not item: return
            # Toggle state
            if item.is_checked:
                item.is_checked = False
                item.checked_by = None
            else:
                item.is_checked = True
                item.checked_by = user_name

        # Re-fetch all to build updated keyboard
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
        await query.answer(f"Updated {item.item_name}")
    except Exception:
        await query.answer()