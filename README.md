# Telegram → Notes

A personal note-taking and daily planning system powered by a Telegram bot and Claude (or Gemini) AI.

Send any message to the bot — it lands in an inbox. Claude processes the inbox on a schedule, categorizes notes into Markdown files, and generates daily plans. All notes are synced to a separate git repository (Obsidian vault).

---

## Features

- Send notes via Telegram — automatically categorized and saved
- Daily plans generated every morning at 9:00
- Interactive todo with checkboxes and progress tasks `[N/M]`
- Notes browser by category
- Git sync for notes (pull on startup, push after each run)
- Optional Gemini backend via `--gemini` flag
- Split deployment: run bot and processor on separate servers

---

## Project structure

```
telegramObsidian/
  main.py                # starts bot + processor together
  bot.py                 # Telegram UI, commands, saves messages to inbox
  processor.py           # AI processing, git sync, scheduler, HTTP API (:8080)
  shared.py              # shared helpers (inbox, todo UI, notes browser)
  gemini_processor.py        # Gemini-based processing backend
  PROCESS_TASK.example.md   # AI prompt template (English, in git)
  PROCESS_TASK.md           # generated prompt in your language (not in git)
  generate_prompt.py        # generates PROCESS_TASK.md from the template
  requirements.txt
  .env.example

  notes/                 # separate git repo (Obsidian vault)
    health.md
    workout.md
    shopping.md
    tasks.md
    ideas.md
    recurring.md         # recurring tasks for daily plans
    daily/
      YYYY-MM-DD.md
```

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in your token and user ID
python generate_prompt.py     # generate your PROCESS_TASK.md in your language
python main.py                # start bot + processor together
```

> If `PROCESS_TASK.md` is missing, `main.py` will refuse to start and remind you to run `generate_prompt.py`.

### Prompt generation

`PROCESS_TASK.example.md` is the English template for the AI processing prompt.
`generate_prompt.py` translates it into your language (set via `PROMPT_LANGUAGE` in `.env`) and saves it as `PROCESS_TASK.md`.
The generated file is personal (contains your local path) and is excluded from git.

```bash
python generate_prompt.py              # translate via Claude (default)
python generate_prompt.py --gemini     # translate via Gemini
python generate_prompt.py --no-translate  # just substitute variables, skip translation
```

### Environment variables

```
TELEGRAM_TOKEN=your_bot_token
ALLOWED_USER_ID=your_telegram_user_id

SCHEDULE_HOUR=9
SCHEDULE_MINUTE=0
STARTUP_DRAIN_SECONDS=5

PROCESSOR_URL=http://localhost:8080
PROCESSOR_PORT=8080

# Prompt language (used by generate_prompt.py)
PROMPT_LANGUAGE=Russian

# Optional: Gemini backend
USE_GEMINI=false
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-1.5-flash
```

---

## Running

**Single machine:**
```bash
python main.py
```

**Split deployment (separate servers):**
```bash
# Server 1 — processor
python processor.py

# Server 2 — bot
PROCESSOR_URL=http://server1:8080 python bot.py
```

**Use Gemini instead of Claude:**
```bash
python main.py --gemini
```

---

## Bot commands

| Command | Action |
|---------|--------|
| `/todo` | Today's plan with interactive buttons |
| `/tomorrow` | Tomorrow's plan |
| `/notes` | Notes browser by category |
| `/process` | Trigger inbox processing now |
| `random` | Random note |
| _(any other text)_ | Save to inbox |

---

## How it works

1. Messages sent to the bot are saved to `inbox.json`
2. On startup and daily at 9:00, the processor reads the inbox
3. Claude (or Gemini) categorizes each message and writes it to the appropriate note file
4. Today's and tomorrow's daily plans are created/updated
5. Notes are committed and pushed to the notes git repository
6. Processed inbox entries are cleared

---

## Dependencies

```
python-telegram-bot==21.6
python-dotenv==1.0.1
apscheduler==3.10.4
aiohttp==3.9.5
google-generativeai>=0.7.0
```
