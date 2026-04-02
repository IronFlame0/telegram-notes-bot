# Telegram → Notes (Claude-powered)

## What is this

A Python server with a Telegram bot for personal note-taking and daily planning.
Messages from Telegram are automatically categorized and saved into Markdown files (Obsidian vault).
Claude runs on a schedule inside the server, processes the inbox, and creates daily plans.

---

## Project structure

```
telegramObsidian/
  main.py                # starts bot + processor together
  bot.py                 # Telegram UI, commands, saves messages to inbox
  processor.py           # Claude, git sync, scheduler, HTTP API (:8080)
  shared.py              # shared helpers (inbox, todo UI, notes browser)
  PROCESS_TASK.md        # Claude prompt: how to process inbox and create plans
  CLAUDE.md              # this file
  inbox.json             # incoming messages buffer (do not commit)
  requirements.txt       # Python dependencies
  .env                   # secrets (do not commit)
  .env.example           # environment variables template
  .gitignore

  notes/                 # separate git repository (Obsidian vault)
    health.md            # health, supplements, intake schedules
    workout.md           # workouts
    shopping.md          # active shopping list
    tasks.md             # tasks and projects
    ideas.md             # ideas
    finance.md           # finances (if needed)
    books.md             # books (if needed)
    recurring.md         # recurring tasks (template for daily plans)
    daily/               # daily plans
      YYYY-MM-DD.md
```

---

## How it works

### On `bot.py` startup:
1. Starts the Telegram bot
2. Waits `STARTUP_DRAIN_SECONDS` (default 5s) — Telegram delivers pending messages
3. `git pull` notes from remote repository
4. Triggers processor → Claude runs (`PROCESS_TASK.md`) — processes everything in inbox
5. APScheduler runs daily at `SCHEDULE_HOUR:SCHEDULE_MINUTE` (default 9:00)

### On each Claude run:
1. Reads `inbox.json`, processes new messages → writes to `notes/<category>.md`
2. Updates today's plan (`notes/daily/<TODAY>.md`) — adds tasks marked "today"
3. Creates/updates tomorrow's plan (`notes/daily/<TOMORROW>.md`) with recurring tasks
4. Clears processed entries from `inbox.json`
5. `git commit + push` notes

---

## inbox.json format

```json
[
  {
    "id": "uuid",
    "text": "message text",
    "timestamp": "2026-04-02T09:00:00",
    "processed": false
  }
]
```

---

## Note entry format in notes/*.md

```markdown
## 2026-04-02

- **Title** — short description
  > Useful context (dosage, details, links)
```

---

## Daily plan format notes/daily/YYYY-MM-DD.md

```markdown
# План на YYYY-MM-DD

## 🏋️ Тренировка
- [ ] Тренировка
- [x] Выполненное дело

## ✅ Дела
- [0/4] Работа — 4 рабочих блока по ~1.5 часа
- [ ] Задача

## 🛒 Покупки
- [ ] Товар

## 💡 Идеи для проработки
- Идея из ideas.md

## 🔔 Напоминания
- Дедлайн или событие
```

Checkbox types:
- `- [ ]` / `- [x]` — regular task
- `- [N/M]` — progress task (tap increments counter, resets to 0 at M)

Plans are written in Russian (user language).

---

## Bot commands

| Command / word | Action |
|----------------|--------|
| `/todo`, `туду`, `сегодня`, `дела`, `план` | Today's plan with interactive buttons |
| `/tomorrow`, `завтра`, `план на завтра` | Tomorrow's plan (text) |
| `/notes`, `заметки` | Notes browser by category |
| `/process`, `обработать`, `запустить` | Trigger inbox processing now |
| `рандом`, `random` | Random note |
| _any other text_ | Save to inbox |

---

## Environment variables (.env)

```
TELEGRAM_TOKEN=your_bot_token
ALLOWED_USER_ID=your_telegram_user_id

SCHEDULE_HOUR=9
SCHEDULE_MINUTE=0
STARTUP_DRAIN_SECONDS=5
PROCESSOR_URL=http://localhost:8080   # bot → processor address
PROCESSOR_PORT=8080                   # processor HTTP port
```

---

## Git repositories

- **Main project** (`telegramObsidian/`) — bot code, prompts
  `git@github.com:IronFlame0/telegram-notes-bot.git`

- **Notes** (`notes/`) — separate repo, Obsidian vault
  `git@github.com:IronFlame0/obsidian-notes.git`
  Auto-synced: pull on startup, push after each processing run.

---

## Running

```bash
cd /Users/vladimirgavrilow/Documents/test/telegramObsidian
pip install -r requirements.txt
cp .env.example .env        # fill in token and user id
python main.py              # start bot + processor together
```

Split deployment (separate servers):
```bash
# Server 1 — processor
python processor.py

# Server 2 — bot
PROCESSOR_URL=http://server1:8080 python bot.py
```

---

## Dependencies

```
python-telegram-bot==21.6
python-dotenv==1.0.1
apscheduler==3.10.4
aiohttp==3.9.5
```

---

## Rule for Claude

**After completing any task — update this file (`CLAUDE.md`):**
- Update project structure if files were added or removed
- Update the Status section (check off completed items, add new ones)
- Update Running and Dependencies sections if they changed

---

## Status

- [x] `bot.py` — Telegram UI
- [x] `processor.py` — Claude, git, scheduler, HTTP API
- [x] `shared.py` — shared helpers
- [x] `main.py` — single command to run both services together
- [x] Split deployment on separate servers (via `PROCESSOR_URL`)
- [x] APScheduler — daily processing at 9:00
- [x] Startup processing (drain → pull → claude → push)
- [x] Daily plans with recurring tasks
- [x] Interactive todo with buttons and progress tasks `[N/M]`
- [x] Notes browser by category
- [x] Git sync for notes (pull on startup, push after processing)
- [ ] Notification when tomorrow's plan is ready
