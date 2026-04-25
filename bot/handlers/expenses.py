from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from bot.database.db_config import AsyncSessionLocal, Expense, User, TripGroup
from bot.utils.logger import setup_logger

logger = setup_logger("ExpenseHandler")

# Lock to block double-clicks on the same message
PROCESSED_MESSAGES = set()


async def _upsert_trip_group(session, chat_id: int):
    """Insert TripGroup if not exists. Race-condition safe."""
    stmt = pg_insert(TripGroup).values(
        chat_id=chat_id
    ).on_conflict_do_nothing(index_elements=['chat_id'])
    await session.execute(stmt)


async def _upsert_user(session, telegram_id: int, name: str, username: str = None):
    """Insert User if not exists. Race-condition safe."""
    stmt = pg_insert(User).values(
        telegram_id=telegram_id,
        name=name,
        username=username
    ).on_conflict_do_nothing(index_elements=['telegram_id'])
    await session.execute(stmt)


async def record_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /paid — sends approval request to all group admins."""
    if update.message.chat.type == 'private':
        await update.message.reply_text("⚠️ Please log expenses inside the group!")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ <b>Usage:</b> /paid &lt;amount&gt; &lt;description&gt;",
            parse_mode='HTML'
        )
        return

    try:
        amount = float(context.args[0])
        description = " ".join(context.args[1:])
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number for amount.")
        return

    chat_id = update.message.chat.id
    user = update.message.from_user

    await update.message.reply_text(
        f"⏳ Sending {user.first_name}'s ₹{amount} expense to admins for approval..."
    )

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
    except Exception as e:
        logger.error(f"❌ ADMIN FETCH ERROR: {e}")
        await update.message.reply_text("❌ Could not fetch admins. Try again.")
        return

    appv_data = f"appv_{chat_id}_{user.id}_{amount}"
    rejt_data = f"rejt_{chat_id}_{user.id}_{amount}"

    keyboard = [[
        InlineKeyboardButton("✅ Approve", callback_data=appv_data),
        InlineKeyboardButton("❌ Reject", callback_data=rejt_data)
    ]]

    admin_msg = (
        f"🔔 <b>Pending Expense Approval</b>\n\n"
        f"📍 <b>Group:</b> {update.message.chat.title}\n"
        f"👤 <b>Who:</b> {user.first_name}\n"
        f"💰 <b>Amount:</b> ₹{amount}\n"
        f"📝 <b>For:</b> {description}\n\n"
        f"⏰ <i>You can approve or reject anytime.</i>"
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
                pass  # Admin may have blocked the bot

    if sent_count == 0:
        await update.message.reply_text(
            "⚠️ Could not reach any admin. Make sure admins have started the bot in DM."
        )


async def handle_expense_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin approve/reject button clicks."""
    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass  # Query may have expired — still process the DB logic

    # Deduplication — prevent double processing if admin clicks twice
    message_id = query.message.message_id
    if message_id in PROCESSED_MESSAGES:
        return
    PROCESSED_MESSAGES.add(message_id)

    admin_name = query.from_user.first_name
    data_parts = query.data.split("_")
    action = data_parts[0]
    target_chat_id = int(data_parts[1])
    target_user_id = int(data_parts[2])
    amount = float(data_parts[3])

    # --- REJECT ---
    if action == "rejt":
        try:
            await query.edit_message_text(f"❌ You rejected ₹{amount}.")
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=f"❌ <b>{admin_name}</b> rejected the expense of ₹{amount}.",
                parse_mode='HTML'
            )
        except Exception:
            PROCESSED_MESSAGES.discard(message_id)
        return

    # --- APPROVE ---
    if action == "appv":
        try:
            # Fetch Telegram user info before opening DB session
            user_info = await context.bot.get_chat(target_user_id)
            payer_name = user_info.first_name
            payer_username = getattr(user_info, 'username', None)

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Step 1: Upsert TripGroup — atomic, no race condition
                    await _upsert_trip_group(session, target_chat_id)

                    # Step 2: Upsert User — atomic, no race condition
                    # on_conflict_do_nothing = if user exists, skip silently
                    await _upsert_user(session, target_user_id, payer_name, payer_username)

                    # Step 3: Flush so FK constraints are satisfied before Expense insert
                    await session.flush()

                    # Step 4: Insert Expense — user guaranteed to exist now
                    session.add(Expense(
                        chat_id=target_chat_id,
                        payer_id=target_user_id,
                        amount=amount,
                        description="Verified Expense",
                        is_verified=True
                    ))
                # Auto-commits here if no exception

            await query.edit_message_text(f"✅ Approved ₹{amount} from {payer_name}.")
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=f"✅ <b>{admin_name}</b> approved ₹{amount} for <b>{payer_name}</b>. 🙏",
                parse_mode='HTML'
            )

        except Exception as e:
            logger.error(f"❌ DB ERROR: {e}")
            await query.edit_message_text(
                "❌ Database error. Please try approving again."
            )
            PROCESSED_MESSAGES.discard(message_id)


async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows settlement breakdown for the group."""
    chat_id = update.message.chat_id

    async with AsyncSessionLocal() as session:
        res_total = await session.execute(
            select(func.sum(Expense.amount))
            .where(Expense.chat_id == chat_id, Expense.is_verified == True)
        )
        total_spent = res_total.scalar() or 0.0

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
    share = total_spent / num_people

    text = (
        f"📊 <b>Settlement Dashboard</b>\n"
        f"💰 <b>Total Spent:</b> ₹{total_spent:,.2f}\n"
        f"👥 <b>Equal Share:</b> ₹{share:,.2f}\n\n"
    )

    for name, paid in user_totals:
        diff = paid - share
        if diff > 0:
            icon, label = "🟢", "gets back"
        elif diff < 0:
            icon, label = "🔴", "owes"
        else:
            icon, label = "⚪", "is settled"
        text += f"{icon} <b>{name}</b>: {label} ₹{abs(diff):,.2f}\n"

    await update.message.reply_text(text, parse_mode='HTML')