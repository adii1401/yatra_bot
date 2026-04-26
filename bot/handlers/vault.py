from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from bot.database.db_config import AsyncSessionLocal, TripDocument, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("VaultHandler")

async def save_to_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or ""
    if "#vault" not in caption.lower(): return
    
    chat_id, user = update.message.chat_id, update.message.from_user
    is_doc = "#doc" in caption.lower()
    
    # 🛠️ FIX: Detect media groups to prevent spam messages
    silent = False
    if update.message.media_group_id:
        group_id = update.message.media_group_id
        if context.bot_data.get(f"mg_{group_id}"):
            silent = True 
        else:
            context.bot_data[f"mg_{group_id}"] = True
            
    if update.message.document:
        file_id = update.message.document.file_id
        file_type = "doc" if is_doc else "photo_file"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id 
        file_type = "doc" if is_doc else "photo"
    else:
        return
    
    clean_caption = caption.lower().replace("#vault", "").replace("#doc", "").strip() or f"Upload_{user.first_name}"
    
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(pg_insert(User).values(telegram_id=user.id, name=user.full_name).on_conflict_do_nothing(index_elements=['telegram_id']))
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id).on_conflict_do_nothing(index_elements=['chat_id']))
                await session.flush()
                
                session.add(TripDocument(
                    chat_id=chat_id, uploader_id=user.id, file_id=file_id, 
                    file_type=file_type, caption=clean_caption
                ))
        
        if not silent:
            icon = "📂" if is_doc else "📸"
            await update.message.reply_text(f"{icon} <b>{clean_caption}</b> secured in full quality!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"vault error: {e}")


async def open_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            docs = (await session.execute(select(TripDocument).where(TripDocument.chat_id == chat_id, TripDocument.file_type == "doc"))).scalars().all()
        
        if not docs:
            await update.message.reply_text("🗄️ No important documents found.")
            return

        for doc in docs:
            keyboard = [[InlineKeyboardButton(f"📥 Get {doc.caption}", callback_data=f"getv_{doc.id}")]]
            await update.message.reply_text(f"📄 {doc.caption}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except Exception as e:
        logger.error(f"open_vault error: {e}")

async def get_vault_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    doc_id = int(query.data.split("_")[1])
    try:
        async with AsyncSessionLocal() as session:
            doc = await session.get(TripDocument, doc_id)
        
        if not doc or doc.chat_id != query.message.chat_id: return
        await context.bot.send_document(chat_id=query.message.chat_id, document=doc.file_id, caption=f"📄 {doc.caption}")
    except Exception as e:
        logger.error(f"Vault retrieval error: {e}")