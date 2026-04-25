from telegram import Update
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
    file_id, file_type = (update.message.document.file_id, "document") if update.message.document else (update.message.photo[-1].file_id, "photo") if update.message.photo else (None, None)
    if not file_id: return
    clean_caption = caption.replace("#vault", "").strip() or f"File from {user.first_name}"
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(pg_insert(User).values(telegram_id=user.id, name=user.full_name, username=user.username).on_conflict_do_nothing(index_elements=['telegram_id']))
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id, trip_name=update.message.chat.title or "New Trip").on_conflict_do_nothing(index_elements=['chat_id']))
                await session.flush()
                session.add(TripDocument(chat_id=chat_id, uploader_id=user.id, file_id=file_id, file_type=file_type, caption=clean_caption))
        await update.message.reply_text(f"🔒 <b>{clean_caption}</b> secured!", parse_mode='HTML')
    except Exception as e:
        logger.error(f"vault error: {e}")

async def open_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            docs = (await session.execute(select(TripDocument, User.name).join(User, TripDocument.uploader_id == User.telegram_id).where(TripDocument.chat_id == chat_id).order_by(TripDocument.uploaded_at.desc()))).all()
        if not docs:
            await update.message.reply_text("🗄️ Vault is empty.")
            return
        msg = "🗄️ <b>Vault</b>\n"
        for row in docs:
            msg += f"{('📄' if row.TripDocument.file_type == 'document' else '📸')} <b>{row.TripDocument.caption}</b>\n👉 <code>/get {row.TripDocument.id}</code>\n\n"
        await update.message.reply_text(msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"open_vault error: {e}")

async def get_vault_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    try:
        doc_id = int(context.args[0])
        async with AsyncSessionLocal() as session:
            doc = await session.get(TripDocument, doc_id)
        if not doc or doc.chat_id != update.message.chat_id: return
        if doc.file_type == "document":
            await context.bot.send_document(chat_id=update.message.chat_id, document=doc.file_id, caption=doc.caption)
        else:
            await context.bot.send_photo(chat_id=update.message.chat_id, photo=doc.file_id, caption=doc.caption)
    except Exception as e:
        logger.error(f"get_vault error: {e}")