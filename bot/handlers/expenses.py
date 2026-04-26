import csv
import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from bot.database.db_config import AsyncSessionLocal, Expense, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("ExpenseHandler")

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
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Ensure user and group exist
                await session.execute(pg_insert(User).values(telegram_id=user.id, name=user.full_name, username=user.username).on_conflict_do_nothing(index_elements=['telegram_id']))
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id, trip_name=update.message.chat.title).on_conflict_do_nothing(index_elements=['chat_id']))
                
                # Create Expense
                expense = Expense(chat_id=chat_id, payer_id=user.id, amount=amount, description=description)
                session.add(expense)
                await session.flush()
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Approve", callback_data=f"exp_yes_{expense.id}_{chat_id}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"exp_no_{expense.id}_{chat_id}")
                    ]
                ]
                await update.message.reply_text(
                    f"💸 <b>{user.first_name}</b> logged an expense:\n\n"
                    f"💰 Amount: ₹{amount}\n📝 For: {description}\n\n"
                    f"Admins, please verify:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
    except Exception as e:
        logger.error(f"Expense save error: {e}")
        await update.message.reply_text("⚠️ Failed to record expense.")

async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    action = data[1]
    expense_id = int(data[2])
    chat_id = int(data[3])

    amount = 0
    desc = ""
    payer_name = "Someone"

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                expense = await session.get(Expense, expense_id)
                if not expense:
                    await query.edit_message_text("⚠️ This expense was not found or already deleted.")
                    return

                if action == "yes":
                    expense.is_verified = True
                    amount = expense.amount
                    desc = expense.description
                    payer = await session.get(User, expense.payer_id)
                    if payer: payer_name = payer.name
                else:
                    await session.delete(expense)
                    amount = expense.amount

        # 🚨 Executed OUTSIDE DB transaction to prevent hangs
        if action == "yes":
            await query.edit_message_text(f"✅ Approved ₹{amount} for '{desc}'")
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ <b>Expense Approved</b>\n₹{amount} by {payer_name} for '{desc}'",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Failed to notify group: {e}")
        else:
            await query.edit_message_text(f"❌ Rejected ₹{amount}")

    except Exception as e:
        logger.error(f"Expense Callback Error: {e}")
        await query.edit_message_text("⚠️ An error occurred processing this request.")

async def set_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    try:
        count = int(context.args[0])
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id, member_count=count).on_conflict_do_update(index_elements=['chat_id'], set_={'member_count': count}))
        await update.message.reply_text(f"✅ Total group members set to {count} for splitting.")
    except Exception:
        await update.message.reply_text("⚠️ Usage: /set_members [number]")

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    try:
        async with AsyncSessionLocal() as session:
            group = await session.get(TripGroup, chat_id)
            total_members = group.member_count if group and group.member_count else 1

            expenses = (await session.execute(
                select(Expense, User.name)
                .join(User, Expense.payer_id == User.telegram_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
            )).all()

        if not expenses:
            await update.message.reply_text("📊 No verified expenses yet.")
            return

        total_spent = sum([row.Expense.amount for row in expenses])
        per_person = total_spent / total_members

        user_totals = {}
        for row in expenses:
            user_totals[row.name] = user_totals.get(row.name, 0) + row.Expense.amount

        msg = f"📊 <b>Trip Expenses</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        msg += f"💰 Total Spent: ₹{total_spent:,.2f}\n"
        msg += f"👥 Per Person ({total_members}): ₹{per_person:,.2f}\n\n"
        
        for name, paid in user_totals.items():
            diff = paid - per_person
            status = f"🟢 Gets back ₹{diff:,.2f}" if diff >= 0 else f"🔴 Owes ₹{abs(diff):,.2f}"
            msg += f"👤 <b>{name}</b> (Paid: ₹{paid:,.2f})\n   {status}\n\n"

        await update.message.reply_text(msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Balance error: {e}")
        await update.message.reply_text("⚠️ Error calculating balances.")

async def export_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private': return
    chat_id = update.message.chat.id
    
    # Check if admin
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        if update.message.from_user.id not in [a.user.id for a in admins]:
            await update.message.reply_text("⚠️ Only admins can export data.")
            return
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        expenses = (await session.execute(
            select(Expense, User.name)
            .join(User, Expense.payer_id == User.telegram_id)
            .where(Expense.chat_id == chat_id, Expense.is_verified == True)
            .order_by(Expense.created_at)
        )).all()

    if not expenses:
        await update.message.reply_text("📊 No expenses to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Payer", "Amount (INR)", "Description"])
    
    for row in expenses:
        dt = row.Expense.created_at.strftime("%Y-%m-%d %H:%M") if row.Expense.created_at else "N/A"
        writer.writerow([dt, row.name, row.Expense.amount, row.Expense.description])

    output.seek(0)
    await context.bot.send_document(chat_id=chat_id, document=io.BytesIO(output.getvalue().encode()), filename="trip_expenses.csv")