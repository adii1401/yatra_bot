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
                # Ensure user and group are registered in the DB
                await session.execute(pg_insert(User).values(telegram_id=user.id, name=user.full_name, username=user.username).on_conflict_do_nothing(index_elements=['telegram_id']))
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id, trip_name=update.message.chat.title).on_conflict_do_nothing(index_elements=['chat_id']))
                
                # Create the expense record
                expense = Expense(chat_id=chat_id, payer_id=user.id, amount=amount, description=description)
                session.add(expense)
                await session.flush()
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Approve", callback_data=f"exp_yes_{expense.id}_{chat_id}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"exp_no_{expense.id}_{chat_id}")
                    ]
                ]
                
                # 📢 Notify the group that the expense is logged and pending
                await update.message.reply_text(f"💸 <b>{user.first_name}</b> logged ₹{amount} for {description}. Pending admin approval.")

                # 🔐 Notify Admins PRIVATELY (The approval buttons no longer appear in the group)
                admins = await context.bot.get_chat_administrators(chat_id)
                for admin in admins:
                    if not admin.user.is_bot:
                        try:
                            await context.bot.send_message(
                                chat_id=admin.user.id,
                                text=f"🛡️ <b>Expense Approval Request</b>\nGroup: {update.message.chat.title}\nUser: {user.full_name}\nAmount: ₹{amount}\nFor: {description}",
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode='HTML'
                            )
                        except Exception:
                            # Skip if the admin hasn't started a private chat with the bot
                            continue
    except Exception as e:
        logger.error(f"Expense save error: {e}")

async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    action = data[1]
    expense_id = int(data[2])
    chat_id = int(data[3])

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                expense = await session.get(Expense, expense_id)
                if not expense:
                    await query.edit_message_text("⚠️ Expense not found.")
                    return

                if action == "yes":
                    expense.is_verified = True
                    payer = await session.get(User, expense.payer_id)
                    payer_name = payer.name if payer else "Someone"
                    msg = f"✅ Approved ₹{expense.amount} by {payer_name}"
                else:
                    msg = f"❌ Rejected ₹{expense.amount}"
                    await session.delete(expense)

        # Notify the original group chat of the outcome
        await query.edit_message_text(msg)
        await context.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error(f"Callback Error: {e}")

async def set_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    try:
        count = int(context.args[0])
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(pg_insert(TripGroup).values(chat_id=chat_id, member_count=count).on_conflict_do_update(index_elements=['chat_id'], set_={'member_count': count}))
        await update.message.reply_text(f"✅ Total human group members set to {count} for splitting. (Excluding the bot)")
    except Exception:
        await update.message.reply_text("⚠️ Usage: /set_members [number]")

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    try:
        async with AsyncSessionLocal() as session:
            # 1. Get the group member count (Manual count ensures the bot is not included)
            group = await session.get(TripGroup, chat_id)
            total_members = group.member_count if group and group.member_count and group.member_count > 0 else 1

            # 2. Fetch all verified expenses
            expenses = (await session.execute(
                select(Expense, User.name)
                .join(User, Expense.payer_id == User.telegram_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
            )).all()

        if not expenses:
            await update.message.reply_text("📊 No verified expenses yet.")
            return

        # 3. Calculate total and per-person share based on the human count
        total_spent = sum([row.Expense.amount for row in expenses])
        per_person = total_spent / total_members

        # 4. Group totals by user
        user_totals = {}
        for row in expenses:
            user_totals[row.name] = user_totals.get(row.name, 0) + row.Expense.amount

        msg = f"📊 <b>Trip Expenses</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        msg += f"👥 Splitting between: <b>{total_members} people</b>\n"
        msg += f"💰 Total Spent: ₹{total_spent:,.2f}\n"
        msg += f"💸 Equal Share: ₹{per_person:,.2f}\n\n"
        
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
    
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        if update.message.from_user.id not in [a.user.id for a in admins]:
            await update.message.reply_text("⚠️ Only admins can export data.")
            return
    except Exception: pass

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