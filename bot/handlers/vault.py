from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from bot.database.db_config import AsyncSessionLocal, TripDocument, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("VaultHandler")


async def save_to_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves documents or photos to the Trip Vault with FK guards."""

    caption = update.message.caption or ""
    if "#vault" not in caption.lower():
        return

    chat_id = update.message.chat_id
    user = update.message.from_user

    if update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    else:
        return

    clean_caption = caption.replace("#vault", "").strip() or f"File from {user.first_name}"

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. FK GUARD: Register User
            await session.execute(pg_insert(User).values(
                telegram_id=user.id,
                name=user.full_name,
                username=user.username
            ).on_conflict_do_nothing(index_elements=['telegram_id']))

            # 2. FK GUARD: Register Trip Group
            await session.execute(pg_insert(TripGroup).values(
                chat_id=chat_id,
                trip_name=update.message.chat.title or "New Expedition"
            ).on_conflict_do_nothing(index_elements=['chat_id']))

            # Flush so Postgres sees parent rows before child insert
            await session.flush()

            # 3. Add to Vault
            session.add(TripDocument(
                chat_id=chat_id,
                uploader_id=user.id,
                file_id=file_id,
                file_type=file_type,
                caption=clean_caption
            ))

    await update.message.reply_text(
        f"🔒 <b>{clean_caption}</b> secured in the Trip Vault!",
        parse_mode='HTML'
    )


async def open_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all stored documents for this group."""
    chat_id = update.message.chat_id

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TripDocument, User.name)
            .join(User, TripDocument.uploader_id == User.telegram_id)
            .where(TripDocument.chat_id == chat_id)
            .order_by(TripDocument.uploaded_at.desc())
        )
        docs = result.all()

    if not docs:
        await update.message.reply_text(
            "🗄️ The Vault is empty. Upload files with caption <b>#vault</b> to store them.",
            parse_mode='HTML'
        )
        return

    msg = "🗄️ <b>Squad Digital Vault</b>\n➖➖➖➖➖➖➖➖➖➖\n"
    for row in docs:
        doc = row.TripDocument
        uploader = row.name
        icon = "📄" if doc.file_type == "document" else "📸"
        msg += f"{icon} <b>{doc.caption}</b> (by {uploader})\n👉 <code>/get {doc.id}</code>\n\n"

    await update.message.reply_text(msg, parse_mode='HTML')


async def get_vault_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the requested file back to the chat."""
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /get <id>\nUse /vault to see file IDs.")
        return

    try:
        doc_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ ID must be a number. Use /vault to see file IDs.")
        return

    async with AsyncSessionLocal() as session:
        doc = await session.get(TripDocument, doc_id)

    if not doc or doc.chat_id != update.message.chat_id:
        await update.message.reply_text("⚠️ File not found in this group's vault.")
        return

    if doc.file_type == "document":
        await context.bot.send_document(
            chat_id=update.message.chat_id,
            document=doc.file_id,
            caption=f"📄 {doc.caption}"
        )
    elif doc.file_type == "photo":
        await context.bot.send_photo(
            chat_id=update.message.chat_id,
            photo=doc.file_id,
            caption=f"📸 {doc.caption}"
        )