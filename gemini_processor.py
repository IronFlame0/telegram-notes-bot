"""
Gemini-based inbox processor.

Unlike Claude (which reads/writes files itself), Gemini only thinks.
Python handles all file I/O: reads inbox + context, sends to Gemini,
parses structured JSON response, writes notes and daily plans.

Activated via USE_GEMINI=true in .env or --gemini CLI flag.
Requires: pip install google-generativeai
Requires: GEMINI_API_KEY in .env
"""

import json
import logging
import os
from datetime import datetime, timedelta

from shared import (
    BASE_DIR, NOTES_DIR, INBOX_FILE,
    load_inbox, save_inbox, unprocessed_items,
)

logger = logging.getLogger(__name__)

DAILY_DIR = os.path.join(NOTES_DIR, "daily")

CATEGORY_FILES = {
    "health":   "health.md",
    "workout":  "workout.md",
    "shopping": "shopping.md",
    "tasks":    "tasks.md",
    "ideas":    "ideas.md",
    "finance":  "finance.md",
    "books":    "books.md",
}


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------

def _read_note(filename: str) -> str:
    path = os.path.join(NOTES_DIR, filename)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _build_context() -> str:
    """Build a summary of existing notes to give Gemini context."""
    parts = []
    for cat, fname in CATEGORY_FILES.items():
        content = _read_note(fname)
        if content:
            parts.append(f"=== {fname} ===\n{content}")
    recurring = _read_note("recurring.md")
    if recurring:
        parts.append(f"=== recurring.md ===\n{recurring}")
    return "\n\n".join(parts)


def _build_prompt(items: list[dict]) -> str:
    today = datetime.now().date().isoformat()
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    today_weekday = datetime.now().weekday()  # 0=Mon, 6=Sun
    tomorrow_weekday = (datetime.now().date() + timedelta(days=1)).weekday()
    today_is_weekday = today_weekday < 5
    tomorrow_is_weekday = tomorrow_weekday < 5

    messages_json = json.dumps([{"id": i["id"], "text": i["text"]} for i in items],
                                ensure_ascii=False, indent=2)
    context = _build_context()

    return f"""You are a personal notes assistant. Today is {today} (tomorrow is {tomorrow}).
Today is {"a weekday" if today_is_weekday else "weekend"}.
Tomorrow is {"a weekday" if tomorrow_is_weekday else "weekend"}.

## Existing notes context
{context}

## New inbox messages to process
{messages_json}

## Your task
Process each inbox message and return a single JSON object with this structure:

{{
  "entries": [
    {{
      "id": "<original message id>",
      "category": "<health|workout|shopping|tasks|ideas|finance|books|other>",
      "category_file": "<filename.md>",
      "date_section": "{today}",
      "title": "<short bold title>",
      "description": "<brief description after —>",
      "comment": "<optional context line starting with >, or null>",
      "for_today": <true if message explicitly says 'сегодня'>,
      "for_tomorrow": <true if message explicitly says 'завтра' or no specific date>
    }}
  ],
  "today_additions": [
    {{
      "section": "<section header like '✅ Дела' or '🏋️ Тренировка'>",
      "task": "<task text for - [ ] >"
    }}
  ],
  "tomorrow_additions": [
    {{
      "section": "<section header>",
      "task": "<task text for - [ ] >"
    }}
  ]
}}

Rules:
- Categorize by meaning: health/supplements → health, sport/exercise → workout, buy/purchase → shopping, todo/reminder → tasks, idea/thought → ideas
- If category file doesn't exist yet, use "other.md"
- for_today=true only if message explicitly contains 'сегодня'
- for_tomorrow=true if message contains 'завтра' OR has no specific date
- today_additions: tasks explicitly for today
- tomorrow_additions: tasks for tomorrow or no specific date
- Write titles and descriptions in Russian
- Return ONLY valid JSON, no markdown, no explanation
"""


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def _append_to_note(category_file: str, date_section: str, title: str,
                     description: str, comment: str | None) -> None:
    path = os.path.join(NOTES_DIR, category_file)

    # Read existing content
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        # Create new file with header
        stem = category_file.replace(".md", "").capitalize()
        content = f"# {stem}\n"

    # Build new entry
    entry_lines = [f"- **{title}**"]
    if description:
        entry_lines[0] += f" — {description}"
    if comment:
        entry_lines.append(f"  > {comment.lstrip('> ')}")
    entry = "\n".join(entry_lines)

    # Find or create date section
    section_header = f"## {date_section}"
    if section_header in content:
        # Append after section header
        insert_pos = content.index(section_header) + len(section_header)
        # Skip to end of section header line
        newline_pos = content.find("\n", insert_pos)
        content = content[:newline_pos + 1] + "\n" + entry + "\n" + content[newline_pos + 1:]
    else:
        # Add new section at end
        content = content.rstrip() + f"\n\n{section_header}\n\n{entry}\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _ensure_daily_dir() -> None:
    os.makedirs(DAILY_DIR, exist_ok=True)


def _append_to_daily(date: str, section: str, task: str) -> None:
    _ensure_daily_dir()
    path = os.path.join(DAILY_DIR, f"{date}.md")

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = [f"# План на {date}\n", "\n"]

    task_line = f"- [ ] {task}\n"
    section_header = f"## {section}\n"

    # Find section and append task
    for i, line in enumerate(lines):
        if line.strip() == section_header.strip():
            # Find insert point: after last task in this section
            j = i + 1
            while j < len(lines) and (lines[j].startswith("- ") or lines[j].strip() == ""):
                j += 1
            lines.insert(j, task_line)
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return

    # Section not found — append at end
    lines.append(f"\n{section_header}{task_line}")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Main Gemini processing function
# ---------------------------------------------------------------------------

async def run_gemini_processing() -> tuple[bool, list[str]]:
    """
    Process inbox using Gemini API.
    Returns (success, list_of_changed_files).
    """
    try:
        import google.generativeai as genai
    except ImportError:
        logger.error("google-generativeai not installed. Run: pip install google-generativeai")
        return False, []

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set in .env")
        return False, []

    items = unprocessed_items()
    if not items:
        return True, []

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        generation_config={"response_mime_type": "application/json"},
    )

    prompt = _build_prompt(items)

    logger.info(f"Sending {len(items)} items to Gemini...")
    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return False, []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Gemini returned invalid JSON: {e}\n{raw[:500]}")
        return False, []

    today = datetime.now().date().isoformat()
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    changed_files = set()

    # Process entries → write to category notes
    processed_ids = set()
    for entry in data.get("entries", []):
        try:
            cat_file = entry.get("category_file", "ideas.md")
            _append_to_note(
                category_file=cat_file,
                date_section=entry.get("date_section", today),
                title=entry["title"],
                description=entry.get("description", ""),
                comment=entry.get("comment"),
            )
            changed_files.add(f"notes/{cat_file}")
            processed_ids.add(entry["id"])
            logger.info(f"Written entry '{entry['title']}' to {cat_file}")
        except Exception as e:
            logger.error(f"Error writing entry {entry}: {e}")

    # Today additions → today's daily plan
    for addition in data.get("today_additions", []):
        try:
            _append_to_daily(today, addition["section"], addition["task"])
            changed_files.add(f"notes/daily/{today}.md")
        except Exception as e:
            logger.error(f"Error writing today addition: {e}")

    # Tomorrow additions → tomorrow's daily plan
    for addition in data.get("tomorrow_additions", []):
        try:
            _append_to_daily(tomorrow, addition["section"], addition["task"])
            changed_files.add(f"notes/daily/{tomorrow}.md")
        except Exception as e:
            logger.error(f"Error writing tomorrow addition: {e}")

    # Mark processed in inbox
    inbox = load_inbox()
    for item in inbox:
        if item["id"] in processed_ids:
            item["processed"] = True
    inbox = [i for i in inbox if not i.get("processed")]
    save_inbox(inbox)

    logger.info(f"Gemini processing done. Changed: {changed_files}")
    return True, sorted(changed_files)
