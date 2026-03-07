# ArkadyJarvis

Multi-user Telegram bot (BotFather, NOT userbot) for team chat summarization, Bitrix24 calendar/CRM, Jira integration, AI assistant, image generation, recruiter scoring (Potok.io), and AI office manager (Glafira via OpenClaw).

## Tech Stack

- **Python 3.11+**, aiogram v3 (Telegram Bot API), FastAPI + Uvicorn
- **AI**: Claude CLI (subscription-based, no API tokens) via subprocess, OpenRouter (Gemini for images, Opus for fallback)
- **Integrations**: Bitrix24 REST API, Jira REST API, Potok.io ATS API, OpenClaw (browser RPA via AI)
- Uvicorn owns the event loop; aiogram polling runs as `asyncio.create_task()` in FastAPI lifespan
- APScheduler for daily summary cron job
- aiosqlite for persistence (users, message buffer, group chats, muted groups)
- pydantic-settings for config from `.env`

## Project Structure

```
app/
  main.py                  # FastAPI app, lifespan, aiogram polling, APScheduler
  config.py                # pydantic-settings (Settings class, reads .env)
  db.py                    # aiosqlite: schema, CRUD (users, group_chats, message_buffer, muted_groups)
  utils.py                 # Parsers (time, attendees, Bitrix datetime), constants, merge_intervals, md_to_telegram_html()
  summarizer.py            # GPT summarization + clean_html_for_telegram()
  version.py               # __version__ = "2.3.0"
  bot/
    create.py              # create_bot() + create_dispatcher() — router registration order matters
    middlewares.py          # ErrorMiddleware (catch-all error handler) + AuthMiddleware (Bitrix auth + muted groups)
    routers/
      start.py             # /start (auto-auth via @username -> Bitrix), /help, MENU_KB (12 buttons), hint callbacks, "Мои встречи"
      summarize.py         # /summary, "суммаризация" trigger
      meeting.py           # "сделай/создай встречу" trigger — time/date/attendee parsing
      free_slots.py        # "найди время" trigger — calendar accessibility + FSM booking (BookSlot)
      jira_task.py         # "сделай/создай задачу" trigger — project key + description
      lead.py              # "сделай/создай лид" trigger — GPT extracts fields -> Bitrix CRM
      image.py             # "нарисуй/сгенерируй" trigger — image generation via OpenRouter/Gemini, supports photo+caption editing
      ask_ai.py            # "спроси ai/вопрос" trigger — Claude answers, md_to_telegram_html conversion
      glafira.py           # Glafira (AI office manager) — FSM chatting mode, OpenClaw streaming
      recruiter.py         # Анатолий (AI recruiter) — Potok.io integration, candidate scoring via Claude
      auto_reply.py        # "ситников" trigger (Seneca quotes via GPT)
      group.py             # on_bot_added / on_bot_removed — tracks group_chats in DB
      buffer.py            # Catch-all (LAST router): buffers all group messages to SQLite
  services/
    ai_client.py           # AIClient — Claude CLI wrapper (subprocess `claude --print`), complete() and chat() methods
    claude_token.py        # Claude OAuth token auto-refresh (file-based data/.claude_token.json)
    bitrix_client/         # BitrixClient — refactored into package with mixins
      __init__.py           # BitrixClient class (combines all mixins)
      _base.py              # _BitrixBase — OAuth file-based tokens, HTTP client, auto-refresh
      _calendar.py          # _BitrixCalendarMixin — calendar events, free slots, create_meeting, get_user_events
      _crm.py               # _BitrixCRMMixin — leads, CRM operations
      _users.py             # _BitrixUsersMixin — user lookup, email guests, find_user_by_nickname
    jira_client.py         # JiraClient — async context manager, single integration user from settings
    openclaw_client.py     # OpenClawClient — HTTP SSE client for OpenClaw gateway
    openrouter_client.py   # OpenRouterClient — image generation (Gemini), ask_opus (Claude Opus via OpenRouter)
    potok_client.py        # PotokClient — Potok.io ATS API (jobs, applicants, scoring push)
    potok_models.py        # Pydantic models: Job, Applicant, Resume, CvParams, ScoringResult, ScoreBreakdown
    resume_scorer.py       # AI candidate scoring — builds prompt from job+applicant, parses JSON response
  scheduler/
    jobs.py                # daily_summary_job — summarizes all groups, builds overview, cleanup
  api/
    routes.py              # GET /api/health
data/
  arkadyjarvis.db          # SQLite database
  bitrix_tokens.json       # Bitrix OAuth tokens (auto-refreshed)
  .claude_token.json       # Claude OAuth tokens (auto-refreshed, single-use refresh tokens)
scripts/
  show_users.py            # CLI: all users + last activity (from message_buffer, 7-day window)
```

## Key Patterns

### Architecture
- **AIClient** wraps Claude CLI (`claude --print --output-format text`) as subprocess. Uses `CLAUDE_CODE_OAUTH_TOKEN` env var. Token auto-refreshed by `claude_token.py` before each call. 120s timeout.
- **BitrixClient** is a singleton, refactored into package with mixins (`_base`, `_users`, `_calendar`, `_crm`). File-based OAuth (`data/bitrix_tokens.json`), auto-refresh on expiry.
- **OpenRouterClient** is a singleton for image generation (Gemini 3 Pro via OpenRouter) and Opus queries.
- **PotokClient** is a singleton for Potok.io ATS API (recruiter functionality).
- **JiraClient** uses a single integration user from settings: `async with JiraClient() as jira:`. Maps Telegram user to Jira reporter/assignee via Bitrix email lookup.
- All persistent state in SQLite via `app/db.py`.
- Services injected into dispatcher in `main.py` lifespan: `dp["ai_client"]`, `dp["bitrix"]`, `dp["openrouter"]`, `dp["openclaw"]`, `dp["potok"]`.
- **ErrorMiddleware** wraps all handlers — catches unhandled exceptions, logs them, replies with generic error.
- **AuthMiddleware** injects `db_user: dict` into every handler's kwargs. Checks muted groups before auth.
- Muted groups: bot collects messages for summarization but blocks all triggers (replies with rejection). Checked in AuthMiddleware before auth logic. CRUD: `db.is_group_muted()`, `db.add_muted_group()`, `db.remove_muted_group()`.

### Router Registration Order (in `create.py`)
Order matters — `buffer.py` must be last (catch-all):
1. start -> 2. summarize -> 3. meeting -> 4. free_slots -> 5. jira_task -> 6. lead -> 7. image -> 8. ask_ai -> 9. glafira -> 10. recruiter -> 11. auto_reply -> 12. group -> 13. buffer

### Authorization Flow
1. User sends `/start` -> bot looks up `@username` in Bitrix field `UF_USR_1678964886664`
2. If found -> saves `(telegram_id, bitrix_user_id, display_name)` to `users` table
3. AuthMiddleware blocks protected commands if user not authorized
4. Public commands: `/start`, `/help` — always allowed without auth

### Free Slots + Booking (FSM)
- `"найди время @nick1 @nick2"` -> computes free slots for 5 business days (9:00-19:00)
- Splits into hourly chunks, builds inline keyboard with slot buttons
- FSM states: `BookSlot.waiting_for_slot` -> `BookSlot.waiting_for_topic`
- User picks slot -> types meeting title -> `BitrixClient.create_meeting()`
- Stale button handler (without StateFilter) shows alert "Кнопки устарели"
- Handler registration order critical: `handle_slot_selected` (with StateFilter) BEFORE `handle_stale_slot`

### Meeting Creation
- Regex: `(?i)^(сделай|создай)\s+встречу`
- `utils.parse_meeting_time()`: supports `HH:MM`, `HHMM`, `DD.MM`, `DD месяц`
- `utils.parse_attendees()`: emails removed from text BEFORE @nick extraction
- @nicks -> `BitrixClient.find_user_by_nickname()` (Bitrix field `UF_USR_1678964886664`)
- Emails -> `BitrixClient.resolve_email_user()`: user.get -> email guest cache -> description fallback

### Bitrix24 Email Guests
- `user.get` **excludes** email-type guests (documented Bitrix limitation)
- Email guests found via `im.user.list.get` — cached in `BitrixClient._email_guests_cache`
- Cannot create email guests via API, only through Bitrix UI

### My Meetings (Мои встречи)
- Button in MENU_KB -> `hint:meetings` callback -> `_show_meetings()` in `start.py`
- Fetches today's events via `bitrix.get_user_events(bitrix_user_id)`
- Displays as inline buttons with time + name, linking to Bitrix calendar event URL

### Image Generation
- Trigger: `"нарисуй/сгенерируй/картинк"` — text or photo with caption
- Uses `OpenRouterClient.generate_image()` via Gemini 3 Pro Image Preview
- Supports photo+caption mode: downloads photo, resizes to max 1024px, sends as base64 alongside prompt
- FSM state `ImageGen.waiting_for_prompt` for button-triggered flow
- Handles multiple response formats from OpenRouter (images array, data URI in string, multimodal content array)

### Ask AI
- Trigger: `"спроси ai/вопрос"` + inline text, or FSM from button
- Uses `AIClient.complete()` (Claude CLI)
- Response converted via `md_to_telegram_html()` from `utils.py`

### Recruiter "Анатолий" (Potok.io Integration)
- **Potok.io** — ATS (Applicant Tracking System) for recruitment
- Access control: `RECRUITER_ALLOWED = {33570147, 367140321, 421632942}` (Artem, Natalya, Liza)
- Flow: hint:recruiter -> load jobs from Potok -> user picks job -> show description + candidate counts -> score new or rescore all
- FSM states: `Recruiter.choosing_job` -> `Recruiter.confirming` -> `Recruiter.scoring`
- **Scoring**: `resume_scorer.py` builds detailed prompt (job desc + applicant resume/experience/skills) -> Claude returns JSON with score 0-100, breakdown by criteria, strengths, weaknesses
- **Recruiter instructions**: job description can contain `"Важно для CLAUDE:"` section — extracted and injected as special instructions into the scoring prompt
- **Score push**: result posted as HTML comment to Potok event + applicant last_name prefixed with `{score:03d}-` for sorting (e.g., `085-Иванов`)
- **Skip scored**: candidates with `^\d{3}-` last_name prefix considered already scored
- Stop button during scoring loop (`recruit:stop` callback)
- Score labels: >=81 "Отлично", >=61 "Хорошо", >=41 "Средне", <41 "Слабо"

### Summarization
- `summarizer.py`: GPT prompt asks for HTML `<b>` tags
- `clean_html_for_telegram()` strips unsupported tags (`<br>`, `<p>`, `<div>`, etc.)
- Keeps only: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a>`

### MENU_KB (Inline Keyboard)
- Defined in `start.py` as `MENU_KB` — 12 buttons in 6 rows:
  - Суммаризация | Встреча
  - Найди время | Задача
  - Лид | Мои встречи
  - Картинка | Спроси AI
  - Глафира | Анатолий
  - Все команды
- Re-sent after every successful action (summarize, meeting, task, lead, booking, image, ask_ai)
- Imported by other routers: `from app.bot.routers.start import MENU_KB`
- Every hint response includes `BACK_MENU_KB` ("◀️ Меню") button for navigation back to main menu

### Daily Summary Job (scheduler/jobs.py)
- Runs at configured time (default 19:00 Novosibirsk)
- Summarizes each enabled group chat separately (summaries NOT sent to groups)
- Builds personalized daily overview per user: filters groups by membership via `bot.get_chat_member()`
- Sends overview **to each active user via DM** (not to group chats)
- `db.get_active_users()` returns all users with `is_active=1`
- Cleans up messages older than 7 days

### Claude CLI (AI Client)
- AIClient calls `claude --print --output-format text` as subprocess
- Token: `CLAUDE_CODE_OAUTH_TOKEN` env var, auto-refreshed via `claude_token.py`
- `claude_token.py`: stores tokens in `data/.claude_token.json`, refresh tokens are single-use (rotate on each refresh), refreshes 10 min before expiry
- `init_token_file()` seeds from `CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_REFRESH_TOKEN` env vars on first run
- `ensure_fresh_token()` called before every CLI invocation
- Optional model override via `CLAUDE_MODEL` setting (e.g., `claude-opus-4-6`)

### OpenRouter
- Used for image generation (Gemini 3 Pro) and Opus queries
- API key: `OPENROUTER_API_KEY`
- `generate_image(prompt, image_b64?)` — returns raw PNG bytes
- `ask_opus(prompt)` — Claude Opus 4.6 via OpenRouter, returns text

### Glafira (AI Office Manager via OpenClaw)
- **OpenClaw** — AI agent that controls browser via prompts (RPA), installed on Mac
- Mac (OpenClaw gateway): Tailscale IP `100.96.205.95:18789`, bind `lan` (0.0.0.0)
- Ubuntu server (Jarvis prod): Tailscale IP `100.109.25.60`
- Gateway auth: token-based (`OPENCLAW_TOKEN`), HTTP endpoint `/v1/chat/completions`
- **OpenClawClient** (`app/services/openclaw_client.py`): HTTP SSE streaming via httpx, `stream_chat(messages)` yields text chunks
- **Glafira router** (`app/bot/routers/glafira.py`): FSM state `Glafira.chatting`, persistent conversation mode (FSM not cleared after each response)
- Access control: `GLAFIRA_ALLOWED = {33570147, 367140321}` (Artem Sitnikov, Natalya Kurland)
- Streaming UX: sends "Думаю..." message, edits it as chunks arrive (throttled: 0.8s between edits, min 20 new chars), `html.escape()` on response
- Exit via `glafira:exit` callback to properly clear FSM state
- Conversation history stored in FSM data, capped at 20 messages
- OpenClaw model: Claude Sonnet 4.6 via OpenRouter

## Database Schema (aiosqlite)

```sql
users (telegram_id PK, bitrix_user_id, bitrix_domain, display_name, is_active, created_at)
group_chats (chat_id PK, chat_title, added_at, summary_enabled)
message_buffer (id PK AUTO, chat_id, sender_id, sender_name, text, sent_at) + INDEX(chat_id, sent_at)
muted_groups (chat_id PK) — groups where bot collects messages but doesn't respond to triggers
```

## Config (.env)

Required: `BOT_TOKEN`

AI: `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_REFRESH_TOKEN` (for auto-refresh), `CLAUDE_CLI_PATH` (default `claude`), `CLAUDE_MODEL` (optional override)

OpenRouter: `OPENROUTER_API_KEY` (for image generation + Opus)

Bitrix24: `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `BITRIX_DOMAIN`, `BITRIX_REFRESH_TOKEN` (first run only)

Potok.io: `POTOK_API_TOKEN`, `POTOK_BASE_URL` (default `https://app.potok.io`)

OpenClaw: `OPENCLAW_URL`, `OPENCLAW_TOKEN`, `OPENCLAW_AGENT_ID` (default `main`)

Jira (integration user): `JIRA_URL`, `JIRA_USERNAME`, `JIRA_PASSWORD`

Other: `DB_PATH` (default `data/arkadyjarvis.db`), `SUMMARY_HOUR` (default 19), `SUMMARY_MINUTE` (default 0), `TIMEZONE` (default `Asia/Novosibirsk`)

## Running

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Health check: `curl localhost:8001/api/health`

Docker: `docker compose up --build` (exposes port 8002)

## Known Issues

- Claude CLI requires `CLAUDE_CODE_OAUTH_TOKEN` — refresh tokens are single-use, lost token = re-auth needed
- Email guests cannot be created via Bitrix REST API (only UI)
- Bitrix OAuth tokens are shared (file-based), not per-user
- Email guest cache is in-memory (resets on restart)
- OpenRouter image generation may silently refuse due to content policy (0 completion tokens = refusal)
- Potok scored candidates identified by `^\d{3}-` last_name prefix — fragile convention
