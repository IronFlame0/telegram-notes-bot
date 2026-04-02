"""
Shared helpers used by both bot and processor services.
No subprocess, no network calls — pure data helpers.
"""

import json
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_FILE = os.path.join(BASE_DIR, "inbox.json")
NOTES_DIR = os.path.join(BASE_DIR, "notes")
PROCESS_TASK_FILE = os.path.join(BASE_DIR, "PROCESS_TASK.md")

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
# Todo helpers
# ---------------------------------------------------------------------------

PROGRESS_RE = re.compile(r"^- \[(\d+)/(\d+)\] (.+)$")


def progress_bar(current: int, total: int) -> str:
    filled = round(current / total * 4) if total else 0
    return "🟦" * filled + "⬜" * (4 - filled)


def parse_tasks(plan_file: str) -> list[dict]:
    tasks = []
    current_section = ""
    with open(plan_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") or stripped.startswith("# "):
            current_section = stripped.lstrip("#").strip()
            continue
        m = PROGRESS_RE.match(stripped)
        if m:
            current, total, text = int(m.group(1)), int(m.group(2)), m.group(3)
            tasks.append({
                "type": "progress", "line_idx": i, "text": text,
                "current": current, "total": total,
                "done": current >= total, "section": current_section,
            })
            continue
        if stripped.startswith("- [ ] ") or stripped.startswith("- [x] "):
            tasks.append({
                "type": "check", "line_idx": i,
                "text": stripped[6:],
                "done": stripped.startswith("- [x] "),
                "section": current_section,
            })
    return tasks


def task_label(task: dict) -> str:
    if task["type"] == "progress":
        if task["done"]:
            return f"✅ {task['text']}"
        return f"{progress_bar(task['current'], task['total'])} {task['text']} {task['current']}/{task['total']}"
    return ("✅" if task["done"] else "🔵") + f" {task['text']}"


def get_sections(tasks: list[dict]) -> list[str]:
    seen = []
    for t in tasks:
        if t["section"] not in seen:
            seen.append(t["section"])
    return seen


def section_summary(tasks: list[dict], section: str) -> str:
    sec = [t for t in tasks if t["section"] == section]
    if not sec:
        return ""
    if all(t["type"] == "progress" for t in sec):
        return "  " + "  ".join(f"{t['current']}/{t['total']}" for t in sec)
    done = sum(1 for t in sec if t["done"])
    return f"  {done}/{len(sec)}" + (" ✅" if done == len(sec) else "")


def build_sections_keyboard(tasks: list[dict], date: str) -> InlineKeyboardMarkup:
    sections = get_sections(tasks)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(s + section_summary(tasks, s), callback_data=f"sec:{date}:{i}")]
        for i, s in enumerate(sections)
    ])


def build_section_keyboard(tasks: list[dict], date: str, section_idx: int) -> InlineKeyboardMarkup:
    section_name = get_sections(tasks)[section_idx]
    rows = [
        [InlineKeyboardButton(task_label(t), callback_data=f"todo:{date}:{t['line_idx']}:{section_idx}")]
        for t in tasks if t["section"] == section_name
    ]
    rows.append([InlineKeyboardButton("← Назад", callback_data=f"back:{date}")])
    return InlineKeyboardMarkup(rows)


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
    section_name = get_sections(tasks)[section_idx]
    lines = [f"{section_name}\n"]
    lines += [task_label(t) for t in tasks if t["section"] == section_name]
    return "\n".join(lines)


def toggle_task(plan_file: str, line_idx: int) -> None:
    with open(plan_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    line = lines[line_idx]
    stripped = line.strip()
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
# Notes browser helpers
# ---------------------------------------------------------------------------

NOTES_EXCLUDE = {"recurring"}

CATEGORY_ICONS = {
    "health": "💊", "shopping": "🛒", "workout": "🏋️",
    "ideas": "💡", "tasks": "✅", "books": "📚", "finance": "💰",
}

NOTE_ENTRY_RE = re.compile(r"^\s*-\s+\*\*(.+?)\*\*\s*(?:—\s*(.+))?$")


def parse_note_entries(fpath: str) -> list[dict]:
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
            comments = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith(">"):
                comments.append(lines[j].strip().lstrip(">").strip())
                j += 1
            body = (desc + ("\n\n" + "\n".join(comments) if comments else "")).strip()
            entries.append({"name": name, "body": body, "section": current_section})
            i = j
            continue
        i += 1
    return entries


def get_note_categories() -> list[tuple[str, str]]:
    cats = []
    for fname in sorted(os.listdir(NOTES_DIR)):
        if not fname.endswith(".md"):
            continue
        stem = fname[:-3]
        if stem in NOTES_EXCLUDE:
            continue
        fpath = os.path.join(NOTES_DIR, fname)
        if not os.path.isfile(fpath) or not parse_note_entries(fpath):
            continue
        cats.append((stem, f"{CATEGORY_ICONS.get(stem, '📝')} {stem.capitalize()}"))
    return cats


def build_notes_categories_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"ncat:{stem}")]
        for stem, label in get_note_categories()
    ])


def build_notes_entries_keyboard(stem: str) -> InlineKeyboardMarkup:
    entries = parse_note_entries(os.path.join(NOTES_DIR, f"{stem}.md"))
    rows = [
        [InlineKeyboardButton(e["name"], callback_data=f"nent:{stem}:{idx}")]
        for idx, e in enumerate(entries)
    ]
    rows.append([InlineKeyboardButton("← Назад", callback_data="nback")])
    return InlineKeyboardMarkup(rows)
