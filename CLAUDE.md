# Telegram → Notes (Claude-powered)

## Что это

Python-сервер с Telegram-ботом для ведения личных заметок и планирования.
Сообщения из Telegram автоматически сортируются по категориям и записываются в Markdown-файлы (Obsidian vault).
Claude запускается по расписанию внутри бота, обрабатывает inbox и создаёт дневной план.

---

## Структура проекта

```
telegramObsidian/
  bot.py                 # Telegram-бот + шедулер + вся логика
  PROCESS_TASK.md        # промпт для Claude: как обрабатывать inbox и создавать планы
  CLAUDE.md              # этот файл
  inbox.json             # буфер входящих сообщений (не коммитить)
  requirements.txt       # зависимости Python
  .env                   # секреты (не коммитить)
  .env.example           # шаблон переменных окружения
  .gitignore

  notes/                 # отдельный git-репозиторий (Obsidian vault)
    health.md            # здоровье, добавки, схемы приёма
    workout.md           # тренировки
    shopping.md          # активный список покупок
    tasks.md             # задачи и проекты
    ideas.md             # идеи
    finance.md           # финансы (если появятся)
    books.md             # книги (если появятся)
    recurring.md         # повторяющиеся задачи (шаблон для планов)
    daily/               # дневные планы
      YYYY-MM-DD.md
```

---

## Как работает

### При старте `bot.py`:
1. Поднимает Telegram-бота
2. Ждёт `STARTUP_DRAIN_SECONDS` (по умолчанию 5 сек) — Telegram доставляет накопленные сообщения
3. `git pull` заметок из удалённого репозитория
4. Запускает Claude (`PROCESS_TASK.md`) — обрабатывает всё накопленное в inbox
5. Стартует APScheduler — ежедневно в `SCHEDULE_HOUR:SCHEDULE_MINUTE` (по умолчанию 9:00)

### При каждом запуске Claude:
1. Читает `inbox.json`, обрабатывает новые сообщения → пишет в `notes/<category>.md`
2. Обновляет сегодняшний план (`notes/daily/<TODAY>.md`) — добавляет задачи "на сегодня"
3. Создаёт/обновляет завтрашний план (`notes/daily/<TOMORROW>.md`) с recurring-задачами
4. Очищает `inbox.json`
5. `git commit + push` заметок

---

## Формат inbox.json

```json
[
  {
    "id": "uuid",
    "text": "текст сообщения",
    "timestamp": "2026-04-02T09:00:00",
    "processed": false
  }
]
```

---

## Формат заметки в notes/*.md

```markdown
## 2026-04-02

- **Название** — краткое описание
  > Полезный контекст (дозировка, детали, ссылки)
```

---

## Формат дневного плана notes/daily/YYYY-MM-DD.md

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

Типы чекбоксов:
- `- [ ]` / `- [x]` — обычная задача
- `- [N/M]` — прогресс-задача (нажатие увеличивает счётчик, на M сбрасывается в 0)

---

## Команды бота

| Команда / слово | Действие |
|----------------|----------|
| `/todo`, `туду`, `сегодня`, `дела`, `план` | План на сегодня с интерактивными кнопками |
| `/tomorrow`, `завтра`, `план на завтра` | План на завтра (текст) |
| `/notes`, `заметки` | Браузер заметок по категориям |
| `/process`, `обработать`, `запустить` | Запустить обработку inbox прямо сейчас |
| `рандом`, `random` | Случайная заметка |
| _любой другой текст_ | Сохранить в inbox |

---

## Переменные окружения (.env)

```
TELEGRAM_TOKEN=your_bot_token
ALLOWED_USER_ID=your_telegram_user_id

SCHEDULE_HOUR=9
SCHEDULE_MINUTE=0
STARTUP_DRAIN_SECONDS=5
```

---

## Git-репозитории

- **Основной проект** (`telegramObsidian/`) — код бота, промпты
  `git@github.com:IronFlame0/telegram-notes-bot.git`

- **Заметки** (`notes/`) — отдельный репо, Obsidian vault
  `git@github.com:IronFlame0/obsidian-notes.git`
  Синхронизируется автоматически: pull при старте, push после каждой обработки.

---

## Запуск

```bash
cd /Users/vladimirgavrilow/Documents/test/telegramObsidian
pip install -r requirements.txt
cp .env.example .env   # заполнить токен и user id
python bot.py
```

---

## Зависимости

```
python-telegram-bot==21.6
python-dotenv==1.0.1
apscheduler==3.10.4
```

---

## Статус

- [x] `bot.py` — Telegram-бот запущен
- [x] Шедулер (APScheduler) — ежедневная обработка в 9:00
- [x] Обработка при старте (pull → drain → claude → push)
- [x] Дневные планы с recurring-задачами
- [x] Интерактивное туду с кнопками и прогресс-задачами
- [x] Браузер заметок по категориям
- [x] git-синхронизация заметок
- [ ] Уведомление при завтрашнем плане готов (push из шедулера)
