import os
import asyncio
import logging
from datetime import datetime, timedelta, time, date
import pytz
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

import db
from parser import parse_task, parse_time_only, TZ

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("planer")

TOKEN = os.environ["TOKEN"]
PORT = int(os.environ.get("PORT", "10000"))

# ──────────────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────────────
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def fmt_date(d: date) -> str:
    today = datetime.now(TZ).date()
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    if d == today + timedelta(days=2):
        return "послезавтра"
    return f"{d.day} {MONTHS[d.month-1]}, {WEEKDAYS[d.weekday()]}"


def fmt_task_line(t) -> str:
    mark = "✅" if t["done"] else "▫️"
    time_part = ""
    if t["remind_at"]:
        local = t["remind_at"].astimezone(TZ)
        time_part = f" ⏰ {local.strftime('%H:%M')}"
    return f"{mark} {t['text']}{time_part}"


def task_buttons(t):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅", callback_data=f"done:{t['id']}"),
        InlineKeyboardButton("⏰", callback_data=f"remind:{t['id']}"),
        InlineKeyboardButton("📅", callback_data=f"move:{t['id']}"),
        InlineKeyboardButton("🗑", callback_data=f"del:{t['id']}"),
    ]])


# ──────────────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────────────
HELP_TEXT = (
    "👋 Планер готов.\n\n"
    "Просто пиши задачу — я добавлю её. По умолчанию на завтра.\n\n"
    "<b>Как указать время:</b>\n"
    "• <code>завтра 15:00 купить корм</code>\n"
    "• <code>сегодня в 20:30 позвонить маме</code>\n"
    "• <code>в 18:00 забрать посылку</code>\n"
    "• <code>через час написать подрядчику</code>\n"
    "• <code>через 30 мин сделать перерыв</code>\n"
    "• <code>послезавтра встреча с клиентом</code>\n\n"
    "<b>Команды:</b>\n"
    "/today — задачи на сегодня\n"
    "/tomorrow — на завтра\n"
    "/week — на неделю\n"
    "/inbox — без даты\n"
    "/help — эта справка\n\n"
    "<b>Расписание:</b>\n"
    "• 22:00 — вечерний пинг «запиши задачи на завтра»\n"
    "• 08:00 — утренний список на день\n"
    "• в указанное время — напоминание о задаче"
)


async def cmd_start(update: Update, _):
    u = update.effective_user
    db.upsert_user(u.id, u.first_name or "")
    await update.message.reply_html(HELP_TEXT)


async def cmd_help(update: Update, _):
    await update.message.reply_html(HELP_TEXT)


async def cmd_today(update: Update, _):
    await send_day_list(update.effective_chat.id, update.effective_user.id,
                        datetime.now(TZ).date(), context=None, msg=update.message)


async def cmd_tomorrow(update: Update, _):
    await send_day_list(update.effective_chat.id, update.effective_user.id,
                        datetime.now(TZ).date() + timedelta(days=1), context=None, msg=update.message)


async def cmd_week(update: Update, _):
    uid = update.effective_user.id
    today = datetime.now(TZ).date()
    end = today + timedelta(days=6)
    tasks = db.tasks_in_range(uid, today, end)
    if not tasks:
        await update.message.reply_text("На неделе пока пусто.")
        return
    # group by date
    by_day = {}
    for t in tasks:
        by_day.setdefault(t["due_date"], []).append(t)
    lines = []
    for d in sorted(by_day):
        lines.append(f"\n<b>{fmt_date(d).capitalize()}</b>")
        for t in by_day[d]:
            lines.append(fmt_task_line(t))
    await update.message.reply_html("\n".join(lines))


async def cmd_inbox(update: Update, _):
    uid = update.effective_user.id
    tasks = db.inbox_tasks(uid)
    if not tasks:
        await update.message.reply_text("Инбокс пустой.")
        return
    await update.message.reply_text(f"📥 Инбокс ({len(tasks)}):")
    for t in tasks:
        await update.message.reply_text(fmt_task_line(t), reply_markup=task_buttons(t))


async def send_day_list(chat_id, user_id, d: date, context, msg=None):
    tasks = db.tasks_for_date(user_id, d)
    header = f"📅 {fmt_date(d).capitalize()}"
    if not tasks:
        text = f"{header}\n\nПусто. Отлично или подозрительно 👀"
        if msg:
            await msg.reply_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return
    if msg:
        await msg.reply_text(f"{header} ({len(tasks)}):")
    else:
        await context.bot.send_message(chat_id, f"{header} ({len(tasks)}):")
    for t in tasks:
        if msg:
            await msg.reply_text(fmt_task_line(t), reply_markup=task_buttons(t))
        else:
            await context.bot.send_message(chat_id, fmt_task_line(t), reply_markup=task_buttons(t))


# ──────────────────────────────────────────────────────
# Text input = add task (unless waiting for reminder time)
# ──────────────────────────────────────────────────────
WAITING_REMIND = {}  # user_id -> task_id


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    # If waiting for reminder time for a task
    if uid in WAITING_REMIND:
        task_id = WAITING_REMIND.pop(uid)
        t = db.get_task(task_id, uid)
        if not t:
            await update.message.reply_text("Задача уже удалена.")
            return
        base = t["due_date"] or datetime.now(TZ).date()
        remind_at = parse_time_only(text, base_date=base)
        if not remind_at:
            await update.message.reply_text("Не понял время. Пример: <code>15:00</code> или <code>через час</code>", parse_mode="HTML")
            WAITING_REMIND[uid] = task_id
            return
        new_date = remind_at.astimezone(TZ).date()
        db.set_task_date(task_id, uid, new_date, remind_at)
        local = remind_at.astimezone(TZ)
        await update.message.reply_html(
            f"⏰ Напомню <b>{fmt_date(new_date)}</b> в <b>{local.strftime('%H:%M')}</b>:\n{t['text']}"
        )
        return

    db.upsert_user(uid, update.effective_user.first_name or "")
    clean, due, remind_at = parse_task(text)
    if not clean:
        return
    task = db.add_task(uid, clean, due, remind_at)

    bits = ["✍️ Записал"]
    if due:
        bits.append(f"на <b>{fmt_date(due)}</b>")
    if remind_at:
        local = remind_at.astimezone(TZ)
        bits.append(f"напомню в <b>{local.strftime('%H:%M')}</b>")
    header = " · ".join(bits)
    await update.message.reply_html(
        f"{header}\n{fmt_task_line(task)}",
        reply_markup=task_buttons(task),
    )


# ──────────────────────────────────────────────────────
# Callback buttons
# ──────────────────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    try:
        action, raw = q.data.split(":", 1)
        task_id = int(raw)
    except Exception:
        return

    t = db.get_task(task_id, uid)
    if not t:
        await q.edit_message_text("Задача уже удалена.")
        return

    if action == "done":
        db.mark_done(task_id, uid)
        await q.edit_message_text(f"✅ <s>{t['text']}</s>", parse_mode="HTML")
    elif action == "del":
        db.delete_task(task_id, uid)
        await q.edit_message_text(f"🗑 Удалено: {t['text']}")
    elif action == "remind":
        WAITING_REMIND[uid] = task_id
        await q.message.reply_html(
            f"⏰ Во сколько напомнить про:\n<b>{t['text']}</b>\n\n"
            "Напиши время (<code>15:00</code>) или <code>через час</code>."
        )
    elif action == "move":
        # Show date options
        today = datetime.now(TZ).date()
        buttons = [
            [InlineKeyboardButton("Сегодня", callback_data=f"setdate:{task_id}:0")],
            [InlineKeyboardButton("Завтра", callback_data=f"setdate:{task_id}:1")],
            [InlineKeyboardButton("Послезавтра", callback_data=f"setdate:{task_id}:2")],
            [InlineKeyboardButton("Через неделю", callback_data=f"setdate:{task_id}:7")],
            [InlineKeyboardButton("В инбокс (без даты)", callback_data=f"setdate:{task_id}:-1")],
        ]
        await q.message.reply_text(
            f"📅 На какую дату перенести: {t['text']}",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    elif action == "setdate":
        parts = raw.split(":")
        task_id = int(parts[0])
        offset = int(parts[1])
        if offset == -1:
            db.set_task_date(task_id, uid, None, None)
            await q.edit_message_text(f"📥 Перенёс в инбокс: {t['text']}")
        else:
            new_date = datetime.now(TZ).date() + timedelta(days=offset)
            # keep remind_at time, shift to new date
            new_remind = None
            if t["remind_at"]:
                local = t["remind_at"].astimezone(TZ)
                new_remind = TZ.localize(datetime.combine(new_date, local.time()))
            db.set_task_date(task_id, uid, new_date, new_remind)
            await q.edit_message_text(f"📅 Перенёс на {fmt_date(new_date)}: {t['text']}")


# ──────────────────────────────────────────────────────
# Scheduled jobs
# ──────────────────────────────────────────────────────
async def job_evening_ping(context: ContextTypes.DEFAULT_TYPE):
    """Ask users to plan tomorrow at 22:00 their local time."""
    for u in db.all_users():
        try:
            tomorrow = datetime.now(TZ).date() + timedelta(days=1)
            existing = db.tasks_for_date(u["user_id"], tomorrow)
            if existing:
                lines = [f"🌙 Задачи на завтра ({len(existing)}):"]
                for t in existing:
                    lines.append(fmt_task_line(t))
                lines.append("\nМожешь добавить ещё — просто напиши.")
                await context.bot.send_message(u["user_id"], "\n".join(lines))
            else:
                await context.bot.send_message(
                    u["user_id"],
                    "🌙 Что нужно сделать завтра?\n\nЗапиши задачи сейчас — утром пришлю список.\n\n"
                    "Можно сразу со временем: <code>в 15:00 созвон с клиентом</code>",
                    parse_mode="HTML",
                )
        except Exception as e:
            log.exception("evening ping failed for %s: %s", u["user_id"], e)


async def job_morning_list(context: ContextTypes.DEFAULT_TYPE):
    """Send today's tasks at 08:00."""
    for u in db.all_users():
        try:
            today = datetime.now(TZ).date()
            tasks = db.tasks_for_date(u["user_id"], today)
            if not tasks:
                continue
            lines = [f"☀️ Доброе утро! Задачи на сегодня ({len(tasks)}):"]
            for t in tasks:
                lines.append(fmt_task_line(t))
            await context.bot.send_message(u["user_id"], "\n".join(lines))
            # Send actionable list
            for t in tasks:
                await context.bot.send_message(u["user_id"], fmt_task_line(t), reply_markup=task_buttons(t))
        except Exception as e:
            log.exception("morning list failed for %s: %s", u["user_id"], e)


async def job_check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Every minute: send due reminders."""
    for t in db.due_reminders():
        try:
            await context.bot.send_message(
                t["user_id"],
                f"🔔 Напоминание:\n\n<b>{t['text']}</b>",
                parse_mode="HTML",
                reply_markup=task_buttons(t),
            )
            db.mark_reminded(t["id"])
        except Exception as e:
            log.exception("reminder failed for task %s: %s", t["id"], e)


# ──────────────────────────────────────────────────────
# Health server (Render Free needs an open port)
# ──────────────────────────────────────────────────────
async def health(_):
    return web.Response(text="ok")


async def run_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health server on :%s", PORT)


# ──────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("today", "задачи на сегодня"),
        BotCommand("tomorrow", "задачи на завтра"),
        BotCommand("week", "на неделю"),
        BotCommand("inbox", "без даты"),
        BotCommand("help", "справка"),
    ])


def build_app():
    db.init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("inbox", cmd_inbox))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    jq = app.job_queue
    jq.run_daily(job_evening_ping, time(22, 0, tzinfo=TZ))
    jq.run_daily(job_morning_list, time(8, 0, tzinfo=TZ))
    jq.run_repeating(job_check_reminders, interval=60, first=10)

    return app


async def main():
    app = build_app()
    await run_health_server()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Bot started: @planerbutkobot")
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
