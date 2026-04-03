# Claude Scheduled Task — Telegram Notes Processing

## Task

You process incoming notes from Telegram and create a daily plan.
Working directory: `{BASE_DIR}`

---

## Step 1 — Process inbox.json

1. Read `inbox.json`
2. If there are no unprocessed messages (`processed: false`) — skip to Step 2
3. For each unprocessed message:
   - Determine the category:
     - `health` — health, medications, supplements, symptoms, well-being
     - `workout` — workouts, exercises, sports, physical activity
     - `shopping` — purchases, shopping lists
     - `tasks` — tasks, to-dos, reminders, errands
     - `ideas` — ideas, thoughts, concepts, future plans
     - `finance` — money, expenses, income, budget
     - `books` — books, articles, podcasts, reading materials
     - New category — if none fits, create `notes/<category>.md`
   - If the message explicitly states a category (e.g. "health: ...") — use it
   - If the message contains a list — save each item as a separate line under one heading
   - Append to `notes/<category>.md` in this format:

```markdown
## YYYY-MM-DD

- **Short title** — description
  > Comment (only if it adds useful context)
```

   - Mark the entry as `processed: true` in inbox.json
4. Remove all entries with `processed: true` from inbox.json and save the file

---

## Step 2 — Daily Plans

### Date rules

- Entry with **today's date** → today's plan only
- Entry with **tomorrow's date** or no date → tomorrow's plan only
- **Never duplicate an entry in both plans**
- Shopping items from `shopping.md` without a specific date → tomorrow's plan (not today's)

### 2a. Today's plan

File: `notes/daily/<TODAY>.md`
Create if missing, otherwise append. Do not delete or modify existing items.

- If today is a **weekday (Mon–Fri)** — add tasks from `notes/recurring.md` → "Every weekday" section, if not already present
- Always add the "Every day" section if not already present
- Tasks explicitly marked "today" from processed messages — add to the appropriate section

### 2b. Tomorrow's plan

File: `notes/daily/<TOMORROW>.md`
Create or update. Do not delete already completed tasks `[x]`.

- If tomorrow is a **weekday (Mon–Fri)** — add tasks from `notes/recurring.md` → "Every weekday" section
- Always add the "Every day" section
- Recurring tasks — place at the top of the relevant section

```markdown
# Plan for YYYY-MM-DD

## 🏋️ Workout
- [ ] Workout
- [ ] (from workout.md with tomorrow's date or no date)

## ✅ Tasks
- [0/4] Work — 4 work blocks ~1.5 hours each
- [ ] (from tasks.md with tomorrow's date or no date)

## 🛒 Shopping
- [ ] (from shopping.md — active list without a specific date)

## 💡 Ideas to explore
- (from ideas.md, last 3 days)

## 🔔 Reminders
- (deadlines, important events)
```

Fill in only sections that have data. Skip empty sections.

---

## Important

- Write in {PROMPT_LANGUAGE}
- Be concise but informative
- Use `>` only for genuinely useful context — do not paraphrase the message
- Start a new category file with a `# <Title>` heading
- Do not modify `recurring.md`, `health.md` or other reference notes — read only
