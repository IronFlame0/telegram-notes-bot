"""
Telegram bot service — UI, commands, message saving.

Communicates with processor via HTTP (PROCESSOR_URL).
Can work with processor on same machine or different server.

Run standalone:
  python bot.py

Run as part of main.py:
  from bot import run
  await run()
"""

import asyncio
import logging
import os
import random
import subprocess
import uuid
from datetime import datetime, timedelta

import aiohttp
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from shared import (
    BASE_DIR, INBOX_FILE, NOTES_DIR, CLEANING_FILE,
    load_inbox, save_inbox,
    CATEGORY_ICONS,
    parse_tasks, get_sections, build_sections_keyboard, build_section_keyboard,
    build_todo_text, build_section_text, toggle_task, reset_tasks,
    build_cleaning_sections_keyboard, build_cleaning_section_keyboard,
    build_notes_categories_keyboard, build_notes_entries_keyboard,
    parse_note_entries,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
STARTUP_DRAIN_SECONDS = int(os.getenv("STARTUP_DRAIN_SECONDS", "5"))
PROCESSOR_URL = os.getenv("PROCESSOR_URL", "http://localhost:8080")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Processor communication
# ---------------------------------------------------------------------------

async def call_processor(path: str, data: dict | None = None, method: str = "GET") -> bool:
    """Call processor HTTP API. Returns True on success."""
    try:
        async with aiohttp.ClientSession() as session:
            timeout = aiohttp.ClientTimeout(total=5)
            url = f"{PROCESSOR_URL}{path}"
            if method == "POST":
                resp = await session.post(url, json=data or {}, timeout=timeout)
            else:
                resp = await session.get(url, timeout=timeout)
            return resp.status == 200
    except aiohttp.ClientError as e:
        logger.error(f"Processor unreachable ({path}): {e}")
        return False


async def save_to_inbox(entry: dict) -> bool:
    """Send message to processor inbox. Fallback to local file if unreachable."""
    ok = await call_processor("/inbox", entry, method="POST")
    if not ok:
        logger.warning("Processor unreachable — saving to local inbox.json")
        inbox = load_inbox()
        inbox.append(entry)
        save_inbox(inbox)
    return ok


async def trigger_processing() -> bool:
    """Ask processor to run Claude now."""
    return await call_processor("/trigger", method="POST")


# ---------------------------------------------------------------------------
# Git pull (bot side — read-only, to refresh /todo and /notes)
# ---------------------------------------------------------------------------

def git_pull_notes() -> str | None:
    notes_git = os.path.join(NOTES_DIR, ".git")
    if not os.path.isdir(notes_git):
        return None
    r = subprocess.run(
        ["git", "pull", "--rebase", "--autostash"],
        cwd=NOTES_DIR, capture_output=True, text=True, timeout=30,
    )
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        logger.error(f"git pull notes failed: {out}")
        return out
    logger.info(f"git pull notes: {out}")
    return None


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

async def _send(edit_message, reply_target, text: str, keyboard) -> None:
    try:
        if edit_message:
            await edit_message.edit_text(text, reply_markup=keyboard)
        else:
            await reply_target.reply_text(text, reply_markup=keyboard)
    except (NetworkError, TimedOut) as e:
        logger.warning(f"Network error: {e}. Retrying in 3s...")
        await asyncio.sleep(3)
        if edit_message:
            await edit_message.edit_text(text, reply_markup=keyboard)
        else:
            await reply_target.reply_text(text, reply_markup=keyboard)


async def send_todo(target, date: str, edit_message=None) -> None:
    plan_file = os.path.join(NOTES_DIR, "daily", f"{date}.md")
    if not os.path.exists(plan_file):
        await _send(edit_message, target,
                    f"📭 План на {date} ещё не создан.\nОн появится после следующей обработки inbox.", None)
        return
    tasks = parse_tasks(plan_file)
    text = build_todo_text(tasks, date)
    keyboard = build_sections_keyboard(tasks, date) if tasks else None
    await _send(edit_message, target, text, keyboard)


async def send_notes_categories(target, edit_message=None) -> None:
    await _send(edit_message, target, "📒 Заметки — выбери категорию:", build_notes_categories_keyboard())


async def send_cleaning(target, edit_message=None) -> None:
    if not os.path.exists(CLEANING_FILE):
        await _send(edit_message, target, "📭 Список уборки не найден.", None)
        return
    tasks = parse_tasks(CLEANING_FILE)
    done = sum(1 for t in tasks if t["done"])
    total = len(tasks)
    text = f"🧹 Уборка — {done}/{total}"
    await _send(edit_message, target, text, build_cleaning_sections_keyboard(tasks))


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

async def handle_todo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("back:"):
        await send_todo(None, data.split(":", 1)[1], edit_message=query.message)
        return

    if data.startswith("sec:"):
        _, date, sec_idx_str = data.split(":")
        plan_file = os.path.join(NOTES_DIR, "daily", f"{date}.md")
        tasks = parse_tasks(plan_file)
        sec_idx = int(sec_idx_str)
        await _send(query.message, None, build_section_text(tasks, sec_idx),
                    build_section_keyboard(tasks, date, sec_idx))
        return

    if data.startswith("todo:"):
        parts = data.split(":")
        date, line_idx_str, sec_idx_str = parts[1], parts[2], parts[3]
        plan_file = os.path.join(NOTES_DIR, "daily", f"{date}.md")
        if not os.path.exists(plan_file):
            await query.answer("Файл плана не найден.", show_alert=True)
            return
        toggle_task(plan_file, int(line_idx_str))
        tasks = parse_tasks(plan_file)
        sec_idx = int(sec_idx_str)
        await _send(query.message, None, build_section_text(tasks, sec_idx),
                    build_section_keyboard(tasks, date, sec_idx))
        return


async def handle_cleaning_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cback":
        await send_cleaning(None, edit_message=query.message)
        return

    if data == "creset":
        if os.path.exists(CLEANING_FILE):
            reset_tasks(CLEANING_FILE)
        await send_cleaning(None, edit_message=query.message)
        return

    if data.startswith("csec:"):
        sec_idx = int(data.split(":")[1])
        tasks = parse_tasks(CLEANING_FILE)
        await _send(query.message, None,
                    build_section_text(tasks, sec_idx),
                    build_cleaning_section_keyboard(tasks, sec_idx))
        return

    if data.startswith("ctodo:"):
        parts = data.split(":")
        line_idx, sec_idx = int(parts[1]), int(parts[2])
        toggle_task(CLEANING_FILE, line_idx)
        tasks = parse_tasks(CLEANING_FILE)
        await _send(query.message, None,
                    build_section_text(tasks, sec_idx),
                    build_cleaning_section_keyboard(tasks, sec_idx))
        return


async def handle_notes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "nback":
        await send_notes_categories(None, edit_message=query.message)
        return

    if data.startswith("ncat:"):
        stem = data.split(":", 1)[1]
        icon = CATEGORY_ICONS.get(stem, "📝")
        await _send(query.message, None,
                    f"{icon} {stem.capitalize()} — выбери заметку:",
                    build_notes_entries_keyboard(stem))
        return

    if data.startswith("nent:"):
        _, stem, idx_str = data.split(":")
        entries = parse_note_entries(os.path.join(NOTES_DIR, f"{stem}.md"))
        entry = entries[int(idx_str)]
        text = f"*{entry['name']}*"
        if entry["section"]:
            text += f"\n_{entry['section']}_"
        if entry["body"]:
            text += f"\n\n{entry['body']}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("← К списку", callback_data=f"ncat:{stem}"),
            InlineKeyboardButton("← К разделам", callback_data="nback"),
        ]])
        await _send(query.message, None, text, keyboard)
        return


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_todo(update.message, datetime.now().date().isoformat())


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_tomorrow(update, context)


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_notes_categories(update.message)


async def cmd_cleaning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_cleaning(update.message)


async def cmd_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok = await trigger_processing()
    if not ok:
        await update.message.reply_text("❌ Процессор недоступен. Проверь, запущен ли processor.py")


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

QUERY_TODAY    = {"туду", "сегодня", "дела", "план"}
QUERY_TOMORROW = {"что завтра", "завтра", "план на завтра"}
QUERY_PROCESS  = {"обработать", "запустить", "обнови", "обновить"}
QUERY_RANDOM   = {"рандом", "случайное", "случайная заметка", "random"}
QUERY_NOTES    = {"заметки", "notes", "мои заметки"}
QUERY_CLEANING = {"уборка", "чистота", "cleaning"}


async def handle_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    plan_file = os.path.join(NOTES_DIR, "daily", f"{tomorrow}.md")
    if not os.path.exists(plan_file):
        await update.message.reply_text(
            f"📭 План на {tomorrow} ещё не создан.\nОн появится после следующей обработки inbox."
        )
        return
    with open(plan_file, "r", encoding="utf-8") as f:
        content = f.read().strip()
    await update.message.reply_text(content or f"📭 Файл плана на {tomorrow} пустой.")


async def handle_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    entries = []
    for fname in os.listdir(NOTES_DIR):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(NOTES_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        category = fname[:-3]
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("- "):
                    entries.append((category, stripped))
    if not entries:
        await update.message.reply_text("📭 Заметок пока нет.")
        return
    category, entry = random.choice(entries)
    await update.message.reply_text(f"🎲 *{category}*\n\n{entry}", parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        logger.warning(f"Blocked user {user_id}")
        return

    text = update.message.text or update.message.caption or ""
    if not text.strip():
        await update.message.reply_text("Пустое сообщение — игнорирую.")
        return

    normalized = text.strip().lower()

    if normalized in QUERY_CLEANING:
        await send_cleaning(update.message)
        return
    if normalized in QUERY_NOTES:
        await send_notes_categories(update.message)
        return
    if normalized in QUERY_RANDOM:
        await handle_random(update, context)
        return
    if normalized in QUERY_TODAY:
        await send_todo(update.message, datetime.now().date().isoformat())
        return
    if normalized in QUERY_TOMORROW:
        await handle_tomorrow(update, context)
        return
    if normalized in QUERY_PROCESS:
        ok = await trigger_processing()
        if not ok:
            await update.message.reply_text("❌ Процессор недоступен.")
        return

    entry = {
        "id": str(uuid.uuid4()),
        "text": text.strip(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "processed": False,
    }
    await save_to_inbox(entry)
    logger.info(f"Saved: {text[:60]}")
    await update.message.reply_text("✅ Сохранено")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def run() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан в .env")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("notes",    cmd_notes))
    app.add_handler(CommandHandler("todo",     cmd_todo))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("process",  cmd_process))
    app.add_handler(CommandHandler("cleaning", cmd_cleaning))
    app.add_handler(CallbackQueryHandler(handle_cleaning_callback, pattern=r"^(csec|ctodo|cback|creset)"))
    app.add_handler(CallbackQueryHandler(handle_notes_callback,    pattern=r"^(ncat|nent|nback)"))
    app.add_handler(CallbackQueryHandler(handle_todo_callback,     pattern=r"^(todo|sec|back):"))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)

    await app.bot.set_my_commands([
        ("todo",     "📋 План на сегодня"),
        ("tomorrow", "📅 План на завтра"),
        ("notes",    "📒 Просмотр заметок"),
        ("cleaning", "🧹 Список уборки"),
        ("process",  "⚙️ Обработать входящие"),
    ])

    await app.bot.send_message(chat_id=ALLOWED_USER_ID, text="🟢 Бот запущен. Собираю входящие...")
    logger.info("Bot started. Collecting pending messages...")

    # Wait for Telegram to deliver pending messages
    await asyncio.sleep(STARTUP_DRAIN_SECONDS)

    # Pull latest notes (for /todo and /notes commands)
    pull_err = await asyncio.get_event_loop().run_in_executor(None, git_pull_notes)
    if pull_err:
        await app.bot.send_message(chat_id=ALLOWED_USER_ID, text=f"⚠️ git pull: {pull_err}")

    # Trigger processor to handle accumulated inbox
    await trigger_processing()

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Bot shutting down...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
