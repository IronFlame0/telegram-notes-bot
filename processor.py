"""
Processor service — Claude processing, git sync, scheduler, HTTP API.

HTTP API:
  GET  /health   — health check
  POST /trigger  — run processing now (async, returns immediately)
  POST /inbox    — add message to inbox

Run standalone:
  python processor.py

Run as part of main.py:
  from processor import run
  await run(startup_process=False)
"""

import asyncio
import logging
import os
import subprocess
from datetime import datetime

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

from shared import (
    BASE_DIR, INBOX_FILE, NOTES_DIR, PROCESS_TASK_FILE,
    load_inbox, save_inbox, unprocessed_items,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "9"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "0"))
PROCESSOR_PORT = int(os.getenv("PROCESSOR_PORT", "8080"))

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Guard against concurrent processing runs
_processing_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Git sync
# ---------------------------------------------------------------------------

def _git(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        ["git"] + args, cwd=NOTES_DIR,
        capture_output=True, text=True, timeout=30,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def notes_git_enabled() -> bool:
    return os.path.isdir(os.path.join(NOTES_DIR, ".git"))


def git_pull() -> str | None:
    if not notes_git_enabled():
        return None
    code, out = _git(["pull", "--rebase", "--autostash"])
    if code != 0:
        logger.error(f"git pull failed: {out}")
        return out
    logger.info(f"git pull: {out}")
    return None


def git_push(message: str = "notes: auto-update") -> str | None:
    if not notes_git_enabled():
        return None
    _git(["add", "."])
    code, out = _git(["commit", "-m", message])
    if code != 0 and "nothing to commit" not in out:
        logger.error(f"git commit failed: {out}")
        return out
    code, out = _git(["push"])
    if code != 0:
        logger.error(f"git push failed: {out}")
        return out
    logger.info(f"git push: {out}")
    return None


# ---------------------------------------------------------------------------
# Notes snapshot (to detect changed files after Claude)
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
    return sorted(
        os.path.relpath(p, BASE_DIR)
        for p, mtime in after.items()
        if p not in before or mtime > before[p]
    )


# ---------------------------------------------------------------------------
# Claude processing
# ---------------------------------------------------------------------------

async def run_claude_processing(bot: Bot) -> None:
    async with _processing_lock:
        items = unprocessed_items()
        if not items:
            logger.info("No unprocessed messages — skipping.")
            await _notify(bot, "📭 Новых сообщений нет.")
            return

        if not os.path.exists(PROCESS_TASK_FILE):
            logger.error("PROCESS_TASK.md not found.")
            return

        count = len(items)
        word = "сообщение" if count == 1 else "сообщения" if count < 5 else "сообщений"
        await _notify(bot, f"⚙️ Обрабатываю {count} {word}...")

        with open(PROCESS_TASK_FILE, "r", encoding="utf-8") as f:
            prompt = f.read()

        # Pull latest before processing
        await asyncio.get_event_loop().run_in_executor(None, git_pull)

        before = snapshot_notes()
        logger.info(f"Running Claude ({count} items)...")

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["claude", "--print", "--dangerously-skip-permissions", prompt],
                    cwd=BASE_DIR, capture_output=True, text=True, timeout=300,
                ),
            )

            if result.returncode == 0:
                logger.info("Claude processing done.")
                changed = changed_notes(before)
                err = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: git_push(f"notes: auto-update {datetime.now().strftime('%Y-%m-%d %H:%M')}"),
                )
                if changed:
                    files_list = "\n".join(f"  • {f}" for f in changed)
                    git_status = "\n📤 Запушено в git" if not err else f"\n⚠️ git push: {err}"
                    await _notify(bot, f"✅ Готово. Файлов: {len(changed)}\n{files_list}{git_status}")
                else:
                    await _notify(bot, "✅ Готово.")
            else:
                logger.error(f"Claude error code {result.returncode}: {result.stderr}")
                await _notify(bot, f"❌ Claude завершился с ошибкой (код {result.returncode}).")

        except FileNotFoundError:
            await _notify(bot, "❌ Claude CLI не найден. Установи Claude Code CLI.")
        except subprocess.TimeoutExpired:
            await _notify(bot, "❌ Claude превысил таймаут (5 минут).")


async def _notify(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(chat_id=ALLOWED_USER_ID, text=text)
    except TelegramError as e:
        logger.error(f"Failed to send Telegram notification: {e}")


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "processor"})


async def handle_trigger(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]
    asyncio.ensure_future(run_claude_processing(bot))
    return web.json_response({"status": "triggered"})


async def handle_inbox(request: web.Request) -> web.Response:
    try:
        entry = await request.json()
        inbox = load_inbox()
        inbox.append(entry)
        save_inbox(inbox)
        logger.info(f"Inbox entry added via HTTP: {entry.get('text', '')[:60]}")
        return web.json_response({"status": "saved"})
    except Exception as e:
        return web.json_response({"status": "error", "detail": str(e)}, status=400)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def run(startup_process: bool = True) -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан в .env")

    bot = Bot(token=TOKEN)

    # HTTP server
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/health", handle_health)
    app.router.add_post("/trigger", handle_trigger)
    app.router.add_post("/inbox", handle_inbox)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PROCESSOR_PORT)
    await site.start()
    logger.info(f"Processor HTTP server started on port {PROCESSOR_PORT}")

    # Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: asyncio.ensure_future(run_claude_processing(bot)),
        trigger=CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id="daily_processing",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started — daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")

    # Startup processing (standalone mode)
    if startup_process:
        await run_claude_processing(bot)

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        logger.info("Processor stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(run(startup_process=True))
    except KeyboardInterrupt:
        pass
