import uuid
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
        admins = await context.bot.get_chat_administrators(chat_id)
    except Exception as e:
        logger.error(f"Admin fetch error: {e}")
        return

    # 🛠️ FIX: Unique ID prevents memory overwrite race conditions
    unique_id = str(uuid.uuid4())[:6]
    context.bot_data[f"desc_{user.id}_{amount}_{unique_id}"] = description

    async with AsyncSessionLocal() as session:
        group = await session.get(TripGroup, chat_id)
        trip_label = group.trip_name if group else "Trip Expense"

    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"appv_{chat_id}_{user.id}_{amount}_{unique_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"rejt_{chat_id}_{user.id}_{amount}_{unique_id}")
    ]]

    admin_msg = (
        f"🔔 <b>Approval Required — {trip_label}</b>\n"
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
    unique_id = data_parts[4] if len(data_parts) > 4 else ""
    
    if action == "rejt":
        try:
            await query.edit_message_text(f"❌ Rejected ₹{amount}.")
            await context.bot.send_message(chat_id=target_chat_id, text=f"❌ Expense of ₹{amount} was rejected.")
        except Exception as e:
            logger.error(f"Rejection UI error: {e}")
        return

    if action == "appv":
        desc_key = f"desc_{target_user_id}_{amount}_{unique_id}" if unique_id else f"desc_{target_user_id}_{amount}"
        description = context.bot_data.get(desc_key, "Trip Expense")
        
        try:
            user_info = await context.bot.get_chat(target_user_id)
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await session.execute(pg_insert(TripGroup).values(chat_id=target_chat_id).on_conflict_do_nothing(index_elements=['chat_id']))
                    await session.execute(pg_insert(User).values(telegram_id=target_user_id, name=user_info.first_name, username=getattr(user_info, 'username', None)).on_conflict_do_nothing(index_elements=['telegram_id']))
                    await session.flush()
                    session.add(Expense(
                        chat_id=target_chat_id, 
                        payer_id=target_user_id, 
                        amount=amount, 
                        description=description, 
                        is_verified=True
                    ))
            
            try:
                await query.edit_message_text(f"✅ Approved ₹{amount}")
                await context.bot.send_message(
                    chat_id=target_chat_id, 
                    text=f"✅ <b>{query.from_user.first_name}</b> approved ₹{amount} for <b>{user_info.first_name}</b>.", 
                    parse_mode='HTML'
                )
            except Exception as e:
                if "Message is not modified" in str(e): pass
                else: raise e

        except Exception as e:
            logger.error(f"Approval error: {e}")
            if "duplicate key" not in str(e).lower():
                await query.edit_message_text("❌ Database error during approval.")


async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    actual_count = (await context.bot.get_chat_member_count(chat_id)) - 1
    
    async with AsyncSessionLocal() as session:
        res_total = await session.execute(select(func.sum(Expense.amount)).where(Expense.chat_id == chat_id, Expense.is_verified == True))
        total = res_total.scalar() or 0
        
        res_users = await session.execute(
            select(User.name, func.sum(Expense.amount))
            .join(Expense, User.telegram_id == Expense.payer_id)
            .where(Expense.chat_id == chat_id, Expense.is_verified == True)
            .group_by(User.name)
        )
        user_totals = res_users.all()

    share = total / actual_count if actual_count > 0 else 0

    msg = (
        f"🏔️ <b>EXPEDITION SETTLEMENT</b>\n"
        f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
        f"💰 <b>Total Spent:</b>  ₹{total:,.2f}\n"
        f"👥 <b>Group Size:</b>   {actual_count} members\n"
        f"⚖️ <b>Per Head:</b>     ₹{share:,.2f}\n"
        f"<code>━━━━━━━━━━━━━━━━━━</code>\n\n"
    )
    
    for name, paid in user_totals:
        diff = paid - share
        icon = "🟢" if diff >= 0 else "🔴"
        msg += f"{icon} <b>{name[:12]:<12}</b>: {'+' if diff >= 0 else ''}₹{diff:,.2f}\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')


async def set_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    try:
        count = int(context.args[0])
        async with AsyncSessionLocal() as session:
            async with session.begin():
                group = await session.get(TripGroup, update.message.chat_id)
                if group: group.member_count = count
        await update.message.reply_text(f"✅ Group size set to {count} for settlements.")
    except ValueError:
        pass


# 🛠️ NEW: Export expenses to CSV
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
    csv_bytes = bytes(output.getvalue(), 'utf-8')
    
    await update.message.reply_document(
        document=csv_bytes, 
        filename="Trip_Expenses_Export.csv",
        caption="📊 <b>Complete Expense Ledger</b>",
        parse_mode="HTML"
    )