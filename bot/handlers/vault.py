from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from bot.database.db_config import AsyncSessionLocal, TripDocument, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("VaultHandler")

async def save_to_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves documents or photos to the Trip Vault if they have the #vault hashtag."""
    
    # Check if the message has a caption and contains '#vault'
    caption = update.message.caption or ""
    if "#vault" not in caption.lower():
        return # Ignore normal chat photos/files

    chat_id = update.message.chat_id
    user = update.message.from_user
    
    # Determine if it's a photo or a document
    if update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id # Get the highest resolution photo
        file_type = "photo"
    else:
        return

    # Clean the caption (remove the #vault tag for the database)
    clean_caption = caption.replace("#vault", "").strip() or f"File from {user.first_name}"

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Ensure Group and User exist (Safety check)
            group = await session.get(TripGroup, chat_id)
            if not group: session.add(TripGroup(chat_id=chat_id))
            
            user_db = await session.get(User, user.id)
            if not user_db: session.add(User(telegram_id=user.id, name=user.first_name))

            # Add to Vault DB
            session.add(TripDocument(
                chat_id=chat_id,
                uploader_id=user.id,
                file_id=file_id,
                file_type=file_type,
                caption=clean_caption
            ))

    await update.message.reply_text(f"🔒 <b>{clean_caption}</b> secured in the Trip Vault!", parse_mode='HTML')


async def open_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retrieves all stored documents for the trip."""
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
        await update.message.reply_text("🗄️ The Vault is empty. Upload files with the caption <b>#vault</b> to store them.", parse_mode='HTML')
        return

    msg = "🗄️ <b>Squad Digital Vault</b>\n➖➖➖➖➖➖➖➖➖➖\n"
    
    for row in docs:
        doc = row.TripDocument
        uploader = row.name
        icon = "📄" if doc.file_type == "document" else "📸"
        # We send back the internal DB ID so users can request the specific file
        msg += f"{icon} <b>{doc.caption}</b> (by {uploader})\n👉 Type <code>/get {doc.id}</code> to retrieve.\n\n"

    await update.message.reply_text(msg, parse_mode='HTML')


async def get_vault_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the requested file back to the chat."""
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /get <file_id_number>\nUse /vault to see file IDs.")
        return
        
    try:
        doc_id = int(context.args[0])
    except ValueError:
        return
        
    async with AsyncSessionLocal() as session:
        doc = await session.get(TripDocument, doc_id)
        
    if not doc or doc.chat_id != update.message.chat_id:
        await update.message.reply_text("⚠️ File not found in this group's vault.")
        return
        
    # Ask Telegram to send the file back using the stored file_id
    if doc.file_type == "document":
        await context.bot.send_document(chat_id=update.message.chat_id, document=doc.file_id, caption=f"Here is your: {doc.caption}")
    elif doc.file_type == "photo":
        await context.bot.send_photo(chat_id=update.message.chat_id, photo=doc.file_id, caption=f"Here is your: {doc.caption}")