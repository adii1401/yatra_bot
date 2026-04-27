import csv
import io
import asyncio
import traceback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from bot.database.db_config import get_safe_session, Expense, User, TripGroup, GroupMember
from bot.utils.logger import setup_logger
from datetime import timedelta

logger = setup_logger("ExpenseHandler")


async def record_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private':
        await update.message.reply_text("⚠️ Please log expenses inside the group!")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: <code>/paid 500 dinner</code>", parse_mode='HTML')
        return

    try:
        amount_raw = float(context.args[0])
        amount = int(amount_raw) if amount_raw.is_integer() else amount_raw
        description = " ".join(context.args[1:])
    except ValueError:
        await update.message.reply_text("⚠️ Amount must be a number.", parse_mode='HTML')
        return

    chat_id = update.message.chat.id
    user = update.message.from_user

    try:
        # ✅ FIX: DB work is fully committed BEFORE any Telegram API calls.
        # This eliminates the Double Charge Bug caused by @db_retry retrying
        # the whole function if Telegram lags on the confirmation message.
        async with get_safe_session() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(User)
                    .values(telegram_id=user.id, name=user.full_name, username=user.username)
                    .on_conflict_do_nothing(index_elements=['telegram_id'])
                )
                await session.execute(
                    pg_insert(TripGroup)
                    .values(chat_id=chat_id, trip_name=update.message.chat.title)
                    .on_conflict_do_nothing(index_elements=['chat_id'])
                )
                await session.execute(
                    pg_insert(GroupMember)
                    .values(chat_id=chat_id, user_id=user.id)
                    .on_conflict_do_nothing(index_elements=['chat_id', 'user_id'])
                )

                expense = Expense(chat_id=chat_id, payer_id=user.id, amount=amount, description=description)
                session.add(expense)
                await session.flush()
                expense_id = expense.id
        # ✅ DB transaction is committed here. Telegram calls below are safe to retry independently.

        keyboard = [[
            InlineKeyboardButton("✅ Approve", callback_data=f"exp_yes_{expense_id}_{chat_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"exp_no_{expense_id}_{chat_id}")
        ]]

        await update.message.reply_text(
            f"💳 <b>{user.first_name}</b> added <b>₹{amount}</b> for <i>{description}</i>.\n"
            f"Checking with admins... ⏳",
            parse_mode='HTML'
        )

        admins = await context.bot.get_chat_administrators(chat_id)
        for admin in admins:
            if not admin.user.is_bot:
                try:
                    await context.bot.send_message(
                        chat_id=admin.user.id,
                        text=f"🛡️ <b>New Expense for Approval</b>\n\n"
                             f"📍 Group: {update.message.chat.title}\n"
                             f"👤 Payer: {user.full_name}\n"
                             f"💰 Amount: ₹{amount}\n"
                             f"📝 Item: {description}",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='HTML'
                    )
                except Exception:
                    continue

    except Exception as e:
        logger.error(f"Expense save error: {e}\n{traceback.format_exc()}")
        await update.message.reply_text("⚠️ Failed to save expense. Please try again.")


async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        return

    data = query.data.split("_")
    action = data[1]
    expense_id = int(data[2])
    chat_id = int(data[3])

    try:
        # ✅ get_safe_session() handles DB wake-up internally; no manual retry loop needed.
        async with get_safe_session() as session:
            async with session.begin():
                expense = await session.get(Expense, expense_id)
                if not expense:
                    await query.edit_message_text("⚠️ Expense not found.")
                    return

                if action == "yes":
                    expense.is_verified = True
                    payer = await session.get(User, expense.payer_id)
                    payer_name = payer.name if payer else "Someone"
                    amt_display = int(expense.amount) if expense.amount % 1 == 0 else expense.amount
                    msg = f"✅ Approved ₹{amt_display} for {expense.description} by {payer_name}"
                else:
                    msg = f"❌ Rejected ₹{expense.amount}"
                    await session.delete(expense)

        await query.edit_message_text(msg)
        await context.bot.send_message(chat_id=chat_id, text=msg)

    except Exception as e:
        logger.error(f"Callback Error: {e}\n{traceback.format_exc()}")
        await query.edit_message_text("⚠️ Database timeout. Please tap Approve again.")


async def set_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_ids = [admin.user.id for admin in admins]
        if user_id not in admin_ids:
            await update.message.reply_text("⚠️ Only group Admins can change the total member count!")
            return
    except Exception as e:
        logger.error(f"Failed to fetch admins: {e}")
        await update.message.reply_text("⚠️ Make me an Admin first so I can verify your permissions.")
        return

    # ✅ 1. SYNTAX CHECK: Separate from the database so it catches bracket typos
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /set_members 4\n(Type the number directly with a space, no brackets!)")
        return
        
    try:
        count = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Please just provide a number. Example: /set_members 4")
        return

    # ✅ 2. DATABASE SAVING: If this fails, it reports a DB error, not a usage error
    try:
        async with get_safe_session() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(TripGroup)
                    .values(chat_id=chat_id, member_count=count)
                    .on_conflict_do_update(index_elements=['chat_id'], set_={'member_count': count})
                )
        await update.message.reply_text(f"✅ Total group members set to {count} for splitting.")
    except Exception as e:
        logger.error(f"Database error in set_members: {e}")
        await update.message.reply_text("⚠️ Database is waking up or had an error. Please type the command again.")

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    try:
        async with get_safe_session() as session:
            group = await session.get(TripGroup, chat_id)
            total_members = group.member_count if group and group.member_count and group.member_count > 0 else 1

            expenses_result = await session.execute(
                select(Expense.amount, User.name)
                .join(User, Expense.payer_id == User.telegram_id)
                .where(Expense.chat_id == chat_id, Expense.is_verified == True)
            )
            expenses = expenses_result.all()

            members_result = await session.execute(
                select(User.name)
                .join(GroupMember, User.telegram_id == GroupMember.user_id)
                .where(GroupMember.chat_id == chat_id)
            )
            known_members = members_result.scalars().all()

        if not expenses:
            await update.message.reply_text("📊 No verified expenses yet.")
            return

        user_totals = {name: 0 for name in known_members}

        for amount, name in expenses:
            user_totals[name] = user_totals.get(name, 0) + float(amount)

        total_spent = sum(user_totals.values())
        per_person = total_spent / total_members

        msg = f"📊 <b>Trip Expenses</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        msg += f"👥 Splitting between: <b>{total_members} people</b>\n"
        msg += f"💰 Total Spent: ₹{total_spent:,.2f}\n"
        msg += f"💸 Equal Share: ₹{per_person:,.2f}\n\n"

        for name, paid in user_totals.items():
            diff = paid - per_person
            status = f"🟢 Gets back ₹{diff:,.2f}" if diff >= 0 else f"🔴 Owes ₹{abs(diff):,.2f}"
            msg += f"👤 <b>{name}</b> (Paid: ₹{paid:,.0f})\n   {status}\n\n"

        if len(user_totals) < total_members:
            msg += f"\n<i>⚠️ Note: You set {total_members} members, but only {len(user_totals)} are registered. Unregistered friends won't appear by name until they interact with me!</i>"

        await update.message.reply_text(msg, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Balance error: {e}\n{traceback.format_exc()}")
        await update.message.reply_text("⚠️ Error calculating balances. Please try again in a moment.")


async def export_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private':
        return

    chat_id = update.message.chat.id

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        if update.message.from_user.id not in [a.user.id for a in admins]:
            await update.message.reply_text("⚠️ Only admins can export data.")
            return
    except Exception:
        pass

    async with get_safe_session() as session:
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
        if row.Expense.created_at:
            ist_time = row.Expense.created_at + timedelta(hours=5, minutes=30)
            dt = ist_time.strftime("%Y-%m-%d %I:%M %p")
        else:
            dt = "N/A"
        writer.writerow([dt, row.name, row.Expense.amount, row.Expense.description])

    output.seek(0)
    await context.bot.send_document(
        chat_id=chat_id,
        document=io.BytesIO(output.getvalue().encode()),
        filename="trip_expenses.csv"
    )