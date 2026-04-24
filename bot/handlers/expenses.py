from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from sqlalchemy import select, func
from bot.database.db_config import AsyncSessionLocal, Expense, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("ExpenseHandler")

# lock to block double-clicks
PROCESSED_MESSAGES = set()

async def record_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /paid and sends to admins. Designed to sit in DMs for days if needed."""
    if update.message.chat.type == 'private':
        await update.message.reply_text("⚠️ Please log expenses inside the group!")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ <b>Usage:</b> /paid &lt;amount&gt; &lt;desc&gt;", parse_mode='HTML')
        return

    try:
        amount = float(context.args[0])
        description = " ".join(context.args[1:])
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return

    chat_id = update.message.chat.id
    user = update.message.from_user
    
    await update.message.reply_text(f"⏳ Sending {user.first_name}'s ₹{amount} expense to admins...")

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
    except Exception as e:
        logger.error(f"❌ ADMIN FETCH ERROR: {e}")
        return

    # Callback data structure: action_chatid_userid_amount
    # Note: Description isn't in callback because of 64-char limit
    appv_data = f"appv_{chat_id}_{user.id}_{amount}"
    rejt_data = f"rejt_{chat_id}_{user.id}_{amount}"
    
    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=appv_data),
        InlineKeyboardButton("❌ Reject", callback_data=rejt_data)
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    admin_msg = (
        f"🔔 <b>Pending Expense</b>\n"
        f"📍 <b>Group:</b> {update.message.chat.title}\n"
        f"👤 <b>Who:</b> {user.first_name}\n"
        f"💰 <b>Amount:</b> ₹{amount}\n"
        f"📝 <b>For:</b> {description}\n"
        f"⏰ <i>You can approve this anytime.</i>"
    )

    for admin in admins:
        if not admin.user.is_bot:
            try:
                await context.bot.send_message(chat_id=admin.user.id, text=admin_msg, reply_markup=reply_markup, parse_mode='HTML')
            except Exception:
                pass

async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # ⚡ Silent answer. If the query is 2 days old, Telegram might throw an error.
    # We ignore it so the database logic can still run.
    try:
        await query.answer() 
    except Exception:
        pass 

    message_id = query.message.message_id
    if message_id in PROCESSED_MESSAGES:
        return
    PROCESSED_MESSAGES.add(message_id)

    admin_name = query.from_user.first_name
    data_parts = query.data.split("_")
    action, target_chat_id, target_user_id, amount = data_parts[0], int(data_parts[1]), int(data_parts[2]), float(data_parts[3])

    if action == "rejt":
        try:
            await query.edit_message_text(f"❌ You rejected ₹{amount}.")
            await context.bot.send_message(chat_id=target_chat_id, text=f"❌ <b>{admin_name}</b> rejected the expense of ₹{amount}.", parse_mode='HTML')
        except Exception:
            PROCESSED_MESSAGES.discard(message_id)
        return

    if action == "appv":
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    group = await session.get(TripGroup, target_chat_id)
                    if not group: session.add(TripGroup(chat_id=target_chat_id))

                    user_info = await context.bot.get_chat(target_user_id)
                    payer_name = user_info.first_name
                    
                    user_db = await session.get(User, target_user_id)
                    if not user_db: session.add(User(telegram_id=target_user_id, name=payer_name))

                    session.add(Expense(
                        chat_id=target_chat_id,
                        payer_id=target_user_id,
                        amount=amount,
                        description="Verified Expense",
                        is_verified=True
                    ))
            
            await query.edit_message_text(f"✅ Approved ₹{amount} from {payer_name}.")
            await context.bot.send_message(chat_id=target_chat_id, text=f"✅ <b>{admin_name}</b> approved ₹{amount} for <b>{payer_name}</b>.", parse_mode='HTML')
        except Exception as e:
            logger.error(f"❌ DB ERROR: {e}")
            await query.edit_message_text("❌ Database Error.")
            PROCESSED_MESSAGES.discard(message_id)

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (Same logic as yours, verified working)
    chat_id = update.message.chat_id
    async with AsyncSessionLocal() as session:
        res_total = await session.execute(select(func.sum(Expense.amount)).where(Expense.chat_id == chat_id, Expense.is_verified == True))
        total_spent = res_total.scalar() or 0.0
        if total_spent == 0:
            await update.message.reply_text("📊 No verified expenses yet!")
            return
        res_users = await session.execute(select(User.name, func.sum(Expense.amount)).join(Expense, User.telegram_id == Expense.payer_id).where(Expense.chat_id == chat_id, Expense.is_verified == True).group_by(User.name))
        user_totals = res_users.all()

    num_people = len(user_totals)
    share = total_spent / num_people
    text = f"📊 <b>Settlement Dashboard</b>\n💰 Total: ₹{total_spent:,.2f}\n👥 Share: ₹{share:,.2f}\n"
    for name, paid in user_totals:
        diff = paid - share
        icon = "🟢" if diff > 0 else "🔴" if diff < 0 else "⚪"
        text += f"{icon} <b>{name}</b>: {'gets back' if diff > 0 else 'owes'} ₹{abs(diff):,.2f}\n"
    await update.message.reply_text(text, parse_mode='HTML')