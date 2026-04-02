import asyncio
import json
import logging
import os
import random
import re
import subprocess
import uuid
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_FILE = os.path.join(BASE_DIR, "inbox.json")
NOTES_DIR = os.path.join(BASE_DIR, "notes")
PROCESS_TASK_FILE = os.path.join(BASE_DIR, "PROCESS_TASK.md")

SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "9"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "0"))
STARTUP_DRAIN_SECONDS = int(os.getenv("STARTUP_DRAIN_SECONDS", "5"))

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inbox helpers
# ---------------------------------------------------------------------------

def load_inbox() -> list:
    if not os.path.exists(INBOX_FILE):
        return []
    with open(INBOX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_inbox(inbox: list) -> None:
    with open(INBOX_FILE, "w", encoding="utf-8") as f:
        json.dump(inbox, f, ensure_ascii=False, indent=2)


def unprocessed_items() -> list:
    return [e for e in load_inbox() if not e.get("processed")]


# ---------------------------------------------------------------------------
# Notes git sync
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str) -> tuple[int, str]:
    """Run a git command in cwd, return (returncode, output)."""
    r = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def notes_git_enabled() -> bool:
    return os.path.isdir(os.path.join(NOTES_DIR, ".git"))


def git_pull_notes() -> str | None:
    """Pull latest notes. Returns error string or None on success."""
    if not notes_git_enabled():
        return None
    code, out = _git(["pull", "--rebase", "--autostash"], NOTES_DIR)
    if code != 0:
        logger.error(f"git pull notes failed: {out}")
        return out
    logger.info(f"git pull notes: {out}")
    return None


def git_push_notes(message: str = "update notes") -> str | None:
    """Stage all, commit if needed, push. Returns error string or None."""
    if not notes_git_enabled():
        return None
    _git(["add", "."], NOTES_DIR)
    code, out = _git(["commit", "-m", message], NOTES_DIR)
    if code != 0 and "nothing to commit" not in out:
        logger.error(f"git commit notes failed: {out}")
        return out
    code, out = _git(["push"], NOTES_DIR)
    if code != 0:
        logger.error(f"git push notes failed: {out}")
        return out
    logger.info(f"git push notes: {out}")
    return None


# ---------------------------------------------------------------------------
# Notes snapshot
# ---------------------------------------------------------------------------

def snapshot_notes() -> dict[str, float]:
    result = {}
    for root, _, files in os.walk(NOTES_DIR):
        for fname in files:
            if fname.endswith(".md"):
                path = os.path.join(root, fname)
                result[path] = os.path.getmtime(path)
    return result


def changed_notes(before: dict[str, float]) -> list[str]:
    after = snapshot_notes()
    changed = []
    for path, mtime in after.items():
        if path not in before or mtime > before[path]:
            changed.append(os.path.relpath(path, BASE_DIR))
    return sorted(changed)


# ---------------------------------------------------------------------------
# Claude processing
# ---------------------------------------------------------------------------

async def run_claude_processing(bot: Bot) -> None:
    items = unprocessed_items()
    if not items:
        logger.info("No unprocessed messages — skipping Claude run.")
        await bot.send_message(chat_id=ALLOWED_USER_ID, text="📭 Новых сообщений нет.")
        return

    if not os.path.exists(PROCESS_TASK_FILE):
        logger.error("PROCESS_TASK.md not found — cannot process.")
        return

    count = len(items)
    await bot.send_message(
        chat_id=ALLOWED_USER_ID,
        text=f"⚙️ Обрабатываю {count} {'сообщение' if count == 1 else 'сообщения' if count < 5 else 'сообщений'}...",
    )

    with open(PROCESS_TASK_FILE, "r", encoding="utf-8") as f:
        prompt = f.read()

    before = snapshot_notes()

    logger.info(f"Running Claude to process {count} items...")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["claude", "--print", "--dangerously-skip-permissions", prompt],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=300,
            ),
        )

        if result.returncode == 0:
            logger.info("Claude processing completed successfully.")
            changed = changed_notes(before)

            # Push notes to git
            err = await asyncio.get_event_loop().run_in_executor(
                None, lambda: git_push_notes(f"notes: auto-update {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            )

            if changed:
                files_list = "\n".join(f"  • {f}" for f in changed)
                git_status = "\n📤 Заметки запушены в git" if not err else f"\n⚠️ git push: {err}"
                await bot.send_message(
                    chat_id=ALLOWED_USER_ID,
                    text=f"✅ Готово. Обновлено файлов: {len(changed)}\n{files_list}{git_status}",
                )
            else:
                await bot.send_message(chat_id=ALLOWED_USER_ID, text="✅ Готово.")
        else:
            logger.error(f"Claude exited with code {result.returncode}:\n{result.stderr}")
            await bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text=f"❌ Claude завершился с ошибкой (код {result.returncode}).",
            )

    except FileNotFoundError:
        msg = "❌ Claude CLI не найден. Установи Claude Code CLI."
        logger.error(msg)
        await bot.send_message(chat_id=ALLOWED_USER_ID, text=msg)
    except subprocess.TimeoutExpired:
        msg = "❌ Claude превысил таймаут (5 минут)."
        logger.error(msg)
        await bot.send_message(chat_id=ALLOWED_USER_ID, text=msg)


# ---------------------------------------------------------------------------
# Todo list helpers
# ---------------------------------------------------------------------------

PROGRESS_RE = re.compile(r"^- \[(\d+)/(\d+)\] (.+)$")


def progress_bar(current: int, total: int) -> str:
    filled = round(current / total * 4) if total else 0
    return "🟦" * filled + "⬜" * (4 - filled)


def parse_tasks(plan_file: str) -> list[dict]:
    """Return list of task dicts. Types: 'check' | 'progress'."""
    tasks = []
    current_section = ""
    with open(plan_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") or stripped.startswith("# "):
            current_section = stripped.lstrip("#").strip()
            continue

        # Progress task: - [N/M] text
        m = PROGRESS_RE.match(stripped)
        if m:
            current, total, text = int(m.group(1)), int(m.group(2)), m.group(3)
            tasks.append({
                "type": "progress",
                "line_idx": i,
                "text": text,
                "current": current,
                "total": total,
                "done": current >= total,
                "section": current_section,
            })
            continue

        # Regular checkbox: - [ ] or - [x]
        if stripped.startswith("- [ ] ") or stripped.startswith("- [x] "):
            done = stripped.startswith("- [x] ")
            text = stripped[6:]
            tasks.append({
                "type": "check",
                "line_idx": i,
                "text": text,
                "done": done,
                "section": current_section,
            })

    return tasks


def task_label(task: dict) -> str:
    if task["type"] == "progress":
        if task["done"]:
            return f"✅ {task['text']}"
        bar = progress_bar(task["current"], task["total"])
        return f"{bar} {task['text']} {task['current']}/{task['total']}"
    else:
        icon = "✅" if task["done"] else "🔵"
        return f"{icon} {task['text']}"


def get_sections(tasks: list[dict]) -> list[str]:
    seen = []
    for t in tasks:
        if t["section"] not in seen:
            seen.append(t["section"])
    return seen


def section_summary(tasks: list[dict], section: str) -> str:
    sec_tasks = [t for t in tasks if t["section"] == section]
    if not sec_tasks:
        return ""
    if all(t["type"] == "progress" for t in sec_tasks):
        # Show progress fractions for progress-only sections
        parts = [f"{t['current']}/{t['total']}" for t in sec_tasks]
        return "  " + "  ".join(parts)
    done = sum(1 for t in sec_tasks if t["done"])
    total = len(sec_tasks)
    return f"  {done}/{total}" + (" ✅" if done == total else "")


def build_sections_keyboard(tasks: list[dict], date: str) -> InlineKeyboardMarkup:
    """Level 1: one button per section with completion summary."""
    sections = get_sections(tasks)
    keyboard = []
    for i, section in enumerate(sections):
        label = section + section_summary(tasks, section)
        keyboard.append([InlineKeyboardButton(label, callback_data=f"sec:{date}:{i}")])
    return InlineKeyboardMarkup(keyboard)


def build_section_keyboard(tasks: list[dict], date: str, section_idx: int) -> InlineKeyboardMarkup:
    """Level 2: task buttons for one section + back button."""
    sections = get_sections(tasks)
    section_name = sections[section_idx]
    sec_tasks = [t for t in tasks if t["section"] == section_name]
    keyboard = []
    for task in sec_tasks:
        keyboard.append([InlineKeyboardButton(
            task_label(task),
            callback_data=f"todo:{date}:{task['line_idx']}:{section_idx}",
        )])
    keyboard.append([InlineKeyboardButton("← Назад", callback_data=f"back:{date}")])
    return InlineKeyboardMarkup(keyboard)


def build_todo_text(tasks: list[dict], date: str) -> str:
    if not tasks:
        return f"📋 План на {date}\n\nЗадач нет."
    lines = [f"📋 План на {date}\n"]
    current_section = None
    for task in tasks:
        if task["section"] != current_section:
            current_section = task["section"]
            lines.append(f"\n{current_section}")
        lines.append(task_label(task))
    return "\n".join(lines)


def build_section_text(tasks: list[dict], section_idx: int) -> str:
    sections = get_sections(tasks)
    section_name = sections[section_idx]
    sec_tasks = [t for t in tasks if t["section"] == section_name]
    lines = [f"{section_name}\n"]
    for task in sec_tasks:
        lines.append(task_label(task))
    return "\n".join(lines)


def toggle_task(plan_file: str, line_idx: int) -> None:
    with open(plan_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    line = lines[line_idx]
    stripped = line.strip()

    # Progress task: increment, wrap back to 0 at max
    m = PROGRESS_RE.match(stripped)
    if m:
        current, total, text = int(m.group(1)), int(m.group(2)), m.group(3)
        new_current = (current + 1) % (total + 1)
        indent = line[: len(line) - len(line.lstrip())]
        lines[line_idx] = f"{indent}- [{new_current}/{total}] {text}\n"
    elif "- [ ] " in line:
        lines[line_idx] = line.replace("- [ ] ", "- [x] ", 1)
    elif "- [x] " in line:
        lines[line_idx] = line.replace("- [x] ", "- [ ] ", 1)

    with open(plan_file, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

QUERY_TOMORROW = {"что завтра", "завтра", "план на завтра"}
QUERY_TODAY = {"туду", "сегодня", "дела", "план"}
QUERY_PROCESS = {"обработать", "запустить", "обнови", "обновить"}
QUERY_RANDOM = {"рандом", "случайное", "случайная заметка", "random"}
QUERY_NOTES = {"заметки", "notes", "мои заметки"}

# Category files to exclude from notes browser
NOTES_EXCLUDE = {"recurring"}

CATEGORY_ICONS = {
    "health":   "💊",
    "shopping": "🛒",
    "workout":  "🏋️",
    "ideas":    "💡",
    "tasks":    "✅",
    "books":    "📚",
    "finance":  "💰",
}


# ---------------------------------------------------------------------------
# Notes browser helpers
# ---------------------------------------------------------------------------

NOTE_ENTRY_RE = re.compile(r"^\s*-\s+\*\*(.+?)\*\*\s*(?:—\s*(.+))?$")


def parse_note_entries(fpath: str) -> list[dict]:
    """Parse **bold** entries from a category note file."""
    entries = []
    current_section = ""
    with open(fpath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.startswith("## ") or stripped.startswith("# "):
            current_section = stripped.lstrip("#").strip()
            i += 1
            continue

        m = NOTE_ENTRY_RE.match(stripped)
        if m:
            name = m.group(1).strip()
            desc = (m.group(2) or "").strip()
            # Grab following comment lines (>)
            comments = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith(">"):
                comments.append(lines[j].strip().lstrip(">").strip())
                j += 1
            body = desc
            if comments:
                body = (body + "\n\n" + "\n".join(comments)).strip()
            entries.append({
                "name": name,
                "body": body,
                "section": current_section,
            })
            i = j
            continue

        i += 1
    return entries


def get_note_categories() -> list[tuple[str, str]]:
    """Return [(filename_stem, display_label), ...] sorted."""
    cats = []
    for fname in sorted(os.listdir(NOTES_DIR)):
        if not fname.endswith(".md"):
            continue
        stem = fname[:-3]
        if stem in NOTES_EXCLUDE:
            continue
        fpath = os.path.join(NOTES_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        if not parse_note_entries(fpath):
            continue  # skip empty / no bold entries
        icon = CATEGORY_ICONS.get(stem, "📝")
        cats.append((stem, f"{icon} {stem.capitalize()}"))
    return cats


def build_notes_categories_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    for stem, label in get_note_categories():
        keyboard.append([InlineKeyboardButton(label, callback_data=f"ncat:{stem}")])
    return InlineKeyboardMarkup(keyboard)


def build_notes_entries_keyboard(stem: str) -> InlineKeyboardMarkup:
    fpath = os.path.join(NOTES_DIR, f"{stem}.md")
    entries = parse_note_entries(fpath)
    keyboard = []
    for idx, entry in enumerate(entries):
        keyboard.append([InlineKeyboardButton(
            entry["name"],
            callback_data=f"nent:{stem}:{idx}",
        )])
    keyboard.append([InlineKeyboardButton("← Назад", callback_data="nback")])
    return InlineKeyboardMarkup(keyboard)


async def send_notes_categories(target, edit_message=None) -> None:
    text = "📒 Заметки — выбери категорию:"
    keyboard = build_notes_categories_keyboard()
    await _send(edit_message, target, text, keyboard)


async def handle_notes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── nback ── back to categories
    if data == "nback":
        await send_notes_categories(None, edit_message=query.message)
        return

    # ── ncat:STEM ── show entries list
    if data.startswith("ncat:"):
        stem = data.split(":", 1)[1]
        icon = CATEGORY_ICONS.get(stem, "📝")
        text = f"{icon} {stem.capitalize()} — выбери заметку:"
        keyboard = build_notes_entries_keyboard(stem)
        await _send(query.message, None, text, keyboard)
        return

    # ── nent:STEM:IDX ── show entry content
    if data.startswith("nent:"):
        _, stem, idx_str = data.split(":")
        fpath = os.path.join(NOTES_DIR, f"{stem}.md")
        entries = parse_note_entries(fpath)
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


async def send_todo(bot_or_message, date: str, edit_message=None) -> None:
    """Level 1: plan text + section buttons."""
    plan_file = os.path.join(NOTES_DIR, "daily", f"{date}.md")

    if not os.path.exists(plan_file):
        text = f"📭 План на {date} ещё не создан.\nОн появится после следующей обработки inbox."
        await _send(edit_message, bot_or_message, text, None)
        return

    tasks = parse_tasks(plan_file)
    text = build_todo_text(tasks, date)
    keyboard = build_sections_keyboard(tasks, date) if tasks else None
    await _send(edit_message, bot_or_message, text, keyboard)


async def handle_todo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── back:DATE ── return to level 1
    if data.startswith("back:"):
        date = data.split(":", 1)[1]
        await send_todo(None, date, edit_message=query.message)
        return

    # ── sec:DATE:SEC_IDX ── open section (level 2)
    if data.startswith("sec:"):
        _, date, sec_idx_str = data.split(":")
        plan_file = os.path.join(NOTES_DIR, "daily", f"{date}.md")
        tasks = parse_tasks(plan_file)
        sec_idx = int(sec_idx_str)
        text = build_section_text(tasks, sec_idx)
        keyboard = build_section_keyboard(tasks, date, sec_idx)
        await _send(query.message, None, text, keyboard)
        return

    # ── todo:DATE:LINE_IDX:SEC_IDX ── toggle task, stay in section
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
        text = build_section_text(tasks, sec_idx)
        keyboard = build_section_keyboard(tasks, date, sec_idx)
        await _send(query.message, None, text, keyboard)
        return


async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_notes_categories(update.message)


async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now().date().isoformat()
    await send_todo(update.message, today)


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_tomorrow(update, context)


async def cmd_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_claude_processing(context.bot)


async def handle_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pick a random entry from any note file (excluding daily plans)."""
    entries = []  # list of (file_label, entry_text)

    for fname in os.listdir(NOTES_DIR):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(NOTES_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        category = fname[:-3]
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Collect bullet items (lines starting with "- ")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- "):
                entries.append((category, stripped))

    if not entries:
        await update.message.reply_text("📭 Заметок пока нет.")
        return

    category, entry = random.choice(entries)
    await update.message.reply_text(f"🎲 *{category}*\n\n{entry}", parse_mode="Markdown")


async def handle_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    plan_file = os.path.join(NOTES_DIR, "daily", f"{tomorrow}.md")

    if not os.path.exists(plan_file):
        await update.message.reply_text(
            f"📭 План на {tomorrow} ещё не создан.\n"
            "Он появится после следующей обработки inbox."
        )
        return

    with open(plan_file, "r", encoding="utf-8") as f:
        content = f.read().strip()

    await update.message.reply_text(content or f"📭 Файл плана на {tomorrow} пустой.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        logger.warning(f"Blocked message from unauthorized user {user_id}")
        return

    text = update.message.text or update.message.caption or ""
    if not text.strip():
        await update.message.reply_text("Пустое сообщение — игнорирую.")
        return

    normalized = text.strip().lower()

    if normalized in QUERY_NOTES:
        await send_notes_categories(update.message)
        return

    if normalized in QUERY_RANDOM:
        await handle_random(update, context)
        return

    if normalized in QUERY_TODAY:
        today = datetime.now().date().isoformat()
        await send_todo(update.message, today)
        return

    if normalized in QUERY_TOMORROW:
        await handle_tomorrow(update, context)
        return

    if normalized in QUERY_PROCESS:
        await run_claude_processing(context.bot)
        return

    entry = {
        "id": str(uuid.uuid4()),
        "text": text.strip(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "processed": False,
    }

    inbox = load_inbox()
    inbox.append(entry)
    save_inbox(inbox)

    logger.info(f"Saved message: {text[:60]}")
    await update.message.reply_text("✅ Сохранено")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан в .env")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("notes", cmd_notes))
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("process", cmd_process))
    app.add_handler(CallbackQueryHandler(handle_notes_callback, pattern=r"^(ncat|nent|nback)"))
    app.add_handler(CallbackQueryHandler(handle_todo_callback, pattern=r"^(todo|sec|back):"))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))

    scheduler = AsyncIOScheduler()

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)

    await app.bot.set_my_commands([
        ("todo",     "📋 План на сегодня"),
        ("tomorrow", "📅 План на завтра"),
        ("notes",    "📒 Просмотр заметок"),
        ("process",  "⚙️ Обработать входящие"),
    ])

    await app.bot.send_message(
        chat_id=ALLOWED_USER_ID,
        text="🟢 Сервер запущен. Собираю входящие сообщения...",
    )
    logger.info("Bot started. Collecting pending Telegram messages...")

    await asyncio.sleep(STARTUP_DRAIN_SECONDS)

    # Pull latest notes before processing
    pull_err = await asyncio.get_event_loop().run_in_executor(None, git_pull_notes)
    if pull_err:
        await app.bot.send_message(chat_id=ALLOWED_USER_ID, text=f"⚠️ git pull заметок: {pull_err}")

    await run_claude_processing(app.bot)

    scheduler.add_job(
        lambda: asyncio.ensure_future(run_claude_processing(app.bot)),
        trigger=CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id="daily_processing",
        name=f"Daily Claude processing at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started — daily processing at "
        f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} local time."
    )

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Bye.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
