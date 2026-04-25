from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest # 🛠️ ADDED: To catch double-clicks
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from bot.database.db_config import AsyncSessionLocal, Expense, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("ExpenseHandler")

async def _upsert_trip_group(session, chat_id: int):
    stmt = pg_insert(TripGroup).values(chat_id=chat_id).on_conflict_do_nothing(index_elements=['chat_id'])
    await session.execute(stmt)

async def _upsert_user(session, telegram_id: int, name: str, username: str = None):
    stmt = pg_insert(User).values(
        telegram_id=telegram_id,
        name=name,
        username=username
    ).on_conflict_do_nothing(index_elements=['telegram_id'])
    await session.execute(stmt)

async def record_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private':
        await update.message.reply_text("⚠️ Please log expenses inside the group!")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: <code>/paid 500 dinner</code>", parse_mode='HTML')
        return

    try:
        amount = float(context.args[0])
        description = " ".join(context.args[1:])
    except ValueError:
        await update.message.reply_text("⚠️ Amount must be a number.", parse_mode='HTML')
        return

    chat_id = update.message.chat.id
    user = update.message.from_user

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
    except Exception as e:
        logger.error(f"Admin fetch error: {e}")
        return

    # Save description to bot_data so it survives until approval
    context.bot_data[f"desc_{user.id}_{amount}"] = description

    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"appv_{chat_id}_{user.id}_{amount}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"rejt_{chat_id}_{user.id}_{amount}")
    ]]

    admin_msg = (
        f"🔔 <b>Expense Approval</b>\n"
        f"👤 <b>Who:</b> {user.first_name}\n"
        f"💰 <b>Amount:</b> ₹{amount:,.2f}\n"
        f"📝 <b>For:</b> {description}\n"
    )

    sent_count = 0
    for admin in admins:
        if not admin.user.is_bot:
            try:
                await context.bot.send_message(
                    chat_id=admin.user.id,
                    text=admin_msg,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
                sent_count += 1
            except Exception:
                continue

    if sent_count == 0:
        await update.message.reply_text("⚠️ Admins must <b>Start</b> the bot in DM first!", parse_mode='HTML')
    else:
        await update.message.reply_text(f"⏳ ₹{amount} sent to {sent_count} admins for approval.")

async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    action, target_chat_id, target_user_id, amount = data_parts[0], int(data_parts[1]), int(data_parts[2]), float(data_parts[3])
    
    if action == "rejt":
        try:
            await query.edit_message_text(f"❌ Rejected ₹{amount}.")
            await context.bot.send_message(chat_id=target_chat_id, text=f"❌ Expense of ₹{amount} was rejected.")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Rejection UI error: {e}")
        return

    if action == "appv":
        description = context.bot_data.get(f"desc_{target_user_id}_{amount}", "Trip Expense")
        try:
            user_info = await context.bot.get_chat(target_user_id)
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await _upsert_trip_group(session, target_chat_id)
                    await _upsert_user(session, target_user_id, user_info.first_name, getattr(user_info, 'username', None))
                    await session.flush()
                    session.add(Expense(
                        chat_id=target_chat_id, 
                        payer_id=target_user_id, 
                        amount=amount, 
                        description=description, 
                        is_verified=True
                    ))
            
            # 🛠️ THE FIX: Specific handling for Telegram UI race conditions
            try:
                await query.edit_message_text(f"✅ Approved ₹{amount}")
                await context.bot.send_message(
                    chat_id=target_chat_id, 
                    text=f"✅ <b>{query.from_user.first_name}</b> approved ₹{amount} for <b>{user_info.first_name}</b>.", 
                    parse_mode='HTML'
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    pass # Ignore if another admin already approved it
                else:
                    raise e

        except Exception as e:
            logger.error(f"Approval error: {e}")
            # Prevent showing error if database already has the record
            if "duplicate key" not in str(e).lower():
                await query.edit_message_text("❌ Database error during approval.")
            else:
                await query.edit_message_text("⚠️ This expense was already processed.")

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    try:
        async with AsyncSessionLocal() as session:
            res_total = await session.execute(
                select(func.sum(Expense.amount)).where(Expense.chat_id == chat_id, Expense.is_verified == True)
            )
            total_spent = res_total.scalar() or 0
            
            if total_spent == 0:
                await update.message.reply_text("📊 No verified expenses yet!")
                return

            res_users = await session.execute(
                select(User.name, func.sum(Expense.amount))
                .join(Expense, User.telegram_id == Expense.payer_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
                .group_by(User.name)
            )
            user_totals = res_users.all()

        num_people = len(user_totals)
        share = total_spent / num_people if num_people > 0 else 0 # 🛠️ Guard against ZeroDivision
        
        text = (
            f"📊 <b>Settlement Dashboard</b>\n"
            f"💰 <b>Total Spent:</b> ₹{total_spent:,.2f}\n"
            f"👥 <b>Equal Share:</b> ₹{share:,.2f}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
        )
        for name, paid in user_totals:
            diff = paid - share
            icon = "🟢" if diff >= 0 else "🔴"
            status = "gets back" if diff >= 0 else "owes"
            text += f"{icon} <b>{name}</b> {status} ₹{abs(diff):,.2f}\n"
            
        await update.message.reply_text(text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Balance error: {e}")
        await update.message.reply_text("⚠️ Error calculating balance.")