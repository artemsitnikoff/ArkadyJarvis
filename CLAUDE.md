# ArkadyJarvis

Multi-user Telegram bot (BotFather, NOT userbot) for team chat summarization, Bitrix24 calendar/CRM, and Jira integration.

## Tech Stack

- **Python 3.11+**, aiogram v3 (Telegram Bot API), FastAPI + Uvicorn, OpenAI GPT-5.2, Bitrix24 REST API, Jira REST API
- Uvicorn owns the event loop; aiogram polling runs as `asyncio.create_task()` in FastAPI lifespan
- APScheduler for daily summary cron job
- aiosqlite for persistence (users, Jira creds, message buffer, group chats)
- pydantic-settings for config from `.env`

## Project Structure

```
app/
  main.py                  # FastAPI app, lifespan, aiogram polling, APScheduler
  config.py                # pydantic-settings (Settings class, reads .env)
  db.py                    # aiosqlite: schema, CRUD (users, jira_credentials, group_chats, message_buffer)
  utils.py                 # Parsers (time, attendees, Bitrix datetime), constants, merge_intervals
  summarizer.py            # GPT summarization + clean_html_for_telegram()
  bot/
    create.py              # create_bot() + create_dispatcher() — router registration order matters
    middlewares.py          # AuthMiddleware — checks Bitrix auth, injects db_user
    routers/
      start.py             # /start (auto-auth via @username → Bitrix), /help, MENU_KB, hint callbacks
      auth.py              # /jira FSM (JiraSetup: url → username → password), /skip
      summarize.py         # /summary, "суммаризация" trigger
      meeting.py           # "сделай/создай встречу" trigger — time/date/attendee parsing
      free_slots.py        # "найди время" trigger — calendar accessibility + FSM booking (BookSlot)
      jira_task.py         # "сделай/создай задачу" trigger — project key + description
      lead.py              # "сделай/создай лид" trigger — GPT extracts fields → Bitrix CRM
      auto_reply.py        # "ситников" trigger (Seneca quotes via GPT)
      group.py             # on_bot_added / on_bot_removed — tracks group_chats in DB
      buffer.py            # Catch-all (LAST router): buffers all group messages to SQLite
  services/
    ai_client.py           # AIClient singleton — complete() and chat() methods
    bitrix_client.py       # BitrixClient singleton — OAuth file-based tokens, all Bitrix API
    jira_client.py         # JiraClient — per-user async context manager, loads creds from DB
  scheduler/
    jobs.py                # daily_summary_job — summarizes all groups, builds overview, cleanup
  api/
    routes.py              # GET /api/health
data/
  arkadyjarvis.db          # SQLite database
  bitrix_tokens.json       # Bitrix OAuth tokens (auto-refreshed)
```

## Key Patterns

### Architecture
- **AIClient** is a singleton (one shared OpenAI client)
- **BitrixClient** is a singleton with file-based OAuth (`data/bitrix_tokens.json`), auto-refresh on expiry
- **JiraClient** is per-user: `async with JiraClient(tg_id) as jira:` — loads creds from DB
- All persistent state in SQLite via `app/db.py`
- AuthMiddleware injects `db_user: dict` into every handler's kwargs

### Router Registration Order (in `create.py`)
Order matters — `buffer.py` must be last (catch-all):
1. start → 2. auth → 3. summarize → 4. meeting → 5. free_slots → 6. jira_task → 7. lead → 8. auto_reply → 9. group → 10. buffer

### Authorization Flow
1. User sends `/start` → bot looks up `@username` in Bitrix field `UF_USR_1678964886664`
2. If found → saves `(telegram_id, bitrix_user_id, display_name)` to `users` table
3. AuthMiddleware blocks protected commands if user not authorized
4. Optionally `/jira` to set up Jira credentials (FSM: url → username → password)

### Free Slots + Booking (FSM)
- `"найди время @nick1 @nick2"` → computes free slots for 5 business days (9:00–19:00)
- Splits into hourly chunks, builds inline keyboard with slot buttons
- FSM states: `BookSlot.waiting_for_slot` → `BookSlot.waiting_for_topic`
- User picks slot → types meeting title → `BitrixClient.create_meeting()`
- Stale button handler (without StateFilter) shows alert "Кнопки устарели"
- Handler registration order critical: `handle_slot_selected` (with StateFilter) BEFORE `handle_stale_slot`

### Meeting Creation
- Regex: `(?i)^(сделай|создай)\s+встречу`
- `utils.parse_meeting_time()`: supports `HH:MM`, `HHMM`, `DD.MM`, `DD месяц`
- `utils.parse_attendees()`: emails removed from text BEFORE @nick extraction
- @nicks → `BitrixClient.find_user_by_nickname()` (Bitrix field `UF_USR_1678964886664`)
- Emails → `BitrixClient.resolve_email_user()`: user.get → email guest cache → description fallback

### Bitrix24 Email Guests
- `user.get` **excludes** email-type guests (documented Bitrix limitation)
- Email guests found via `im.user.list.get` — cached in `BitrixClient._email_guests_cache`
- Cannot create email guests via API, only through Bitrix UI

### Summarization
- `summarizer.py`: GPT prompt asks for HTML `<b>` tags
- `clean_html_for_telegram()` strips unsupported tags (`<br>`, `<p>`, `<div>`, etc.)
- Keeps only: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a>`

### MENU_KB (Inline Keyboard)
- Defined in `start.py` as `MENU_KB` — 6 buttons with `callback_data="hint:..."`
- Re-sent after every successful action (summarize, meeting, task, lead, booking)
- Imported by other routers: `from app.bot.routers.start import MENU_KB`
- Every hint response includes `BACK_MENU_KB` ("◀️ Меню") button for navigation back to main menu

### Daily Summary Job (scheduler/jobs.py)
- Runs at configured time (default 19:00 Novosibirsk)
- Summarizes each enabled group chat separately (summaries NOT sent to groups)
- Builds personalized daily overview per user: filters groups by membership via `bot.get_chat_member()`
- Sends overview **to each active user via DM** (not to group chats)
- `db.get_active_users()` returns all users with `is_active=1`
- Cleans up messages older than 7 days

### OpenAI
- Model: `gpt-5.2` — uses `max_completion_tokens` (NOT `max_tokens`)
- VPN required (403 without it from unsupported region)
- AIClient methods: `complete(prompt)`, `chat(messages)`, `.raw` for direct access

## Database Schema (aiosqlite)

```sql
users (telegram_id PK, bitrix_user_id, bitrix_domain, display_name, is_active, created_at)
jira_credentials (telegram_id PK FK, jira_url, jira_username, jira_password)
group_chats (chat_id PK, chat_title, added_at, summary_enabled)
message_buffer (id PK AUTO, chat_id, sender_id, sender_name, text, sent_at) + INDEX(chat_id, sent_at)
```

## Config (.env)

Required: `BOT_TOKEN`, `OPENAI_API_KEY`, `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`

First run: `BITRIX_REFRESH_TOKEN` (for initial OAuth token exchange)

Optional: `BITRIX_DOMAIN`, `OPENAI_MODEL` (default `gpt-5.2`), `DB_PATH` (default `data/arkadyjarvis.db`), `SUMMARY_HOUR` (default 19), `SUMMARY_MINUTE` (default 0), `TIMEZONE` (default `Asia/Novosibirsk`)

## Running

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Health check: `curl localhost:8001/api/health`

Docker: `docker compose up --build` (exposes port 8002)

## Known Issues

- OpenAI API returns 403 without VPN (unsupported region)
- Email guests cannot be created via Bitrix REST API (only UI)
- Bitrix OAuth tokens are shared (file-based), not per-user
- Message buffer and email guest cache reset on restart (buffer is in SQLite, but cache is in-memory)
