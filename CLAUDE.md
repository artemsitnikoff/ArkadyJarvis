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
  version.py               # __version__
  bot/
    create.py              # create_bot() + create_dispatcher() — router registration order matters
    middlewares.py          # ErrorMiddleware (catch-all error handler) + AuthMiddleware (Bitrix auth + muted groups)
    routers/
      start.py             # /start (auto-auth via @username -> Bitrix), /help, MENU_KB (12 buttons), hint callbacks, "Мои встречи"
      summarize.py         # /summary command — on-demand chat summarization
      meeting.py           # FSM MeetingSetup — time/date/attendee parsing, Bitrix meeting creation
      free_slots.py        # FSM BookSlot — calendar accessibility + slot booking
      jira_task.py         # FSM CreateTask — Jira issue creation; raw input is reformatted via prompts/jira_task_template.md before ticket creation
      lead.py              # FSM CreateLead — AI extracts fields -> Bitrix CRM with SOURCE + Telegram contact
      image.py             # FSM ImageGen — image generation via OpenRouter/Gemini, supports photo+caption editing
      ask_ai.py            # FSM AskAI — Claude answers, md_to_telegram_html conversion
      contract.py          # FSM ContractCheck — parse PDF/DOCX/TXT, check against rules in prompts/contract_check.md
      cicero.py            # FSM Cicero — legal consultant (RU law), persistent chat with optional document attachments
      glafira.py           # Glafira (AI office manager) — FSM chatting mode, OpenClaw streaming
      recruiter.py         # Анатолий (AI recruiter) — Potok.io integration, candidate scoring via Claude
      group.py             # on_bot_added / on_bot_removed — tracks group_chats in DB
      buffer.py            # Catch-all (LAST router): buffers all group messages to SQLite
  services/
    ai_client.py           # AIClient — Claude CLI wrapper (subprocess `claude --print`), configurable timeout (default 120s, scorer uses 300s)
    claude_token.py        # Claude OAuth token auto-refresh (file-based data/.claude_token.json)
    bitrix_client/         # BitrixClient — refactored into package with mixins
      __init__.py           # BitrixClient class (combines all mixins)
      _base.py              # _BitrixBase — OAuth file-based tokens, HTTP client, auto-refresh
      _calendar.py          # _BitrixCalendarMixin — calendar events, free slots, create_meeting, get_user_events (today only)
      _crm.py               # _BitrixCRMMixin — leads, CRM operations
      _timeman.py           # _BitrixTimemanMixin — work day start/status via timeman API
      _users.py             # _BitrixUsersMixin — user lookup, email guests, find_user_by_nickname, get_my_team
    jira_client.py         # JiraClient — async context manager, single integration user from settings
    document_parser.py     # Extract text from .pdf/.docx/.txt for contract check
    openclaw_client.py     # OpenClawClient — HTTP SSE client for OpenClaw gateway (per-user agent isolation via user_id)
    openrouter_client.py   # OpenRouterClient — image generation (Gemini)
    prompts.py             # load_prompt(name) — loads templates from prompts/ directory
    potok_client.py        # PotokClient — Potok.io ATS API (jobs, applicants via ajs_joins, scoring push)
    potok_models.py        # Pydantic models: Job, Applicant, Resume, CvParams, ScoringResult, ScoreBreakdown
    resume_scorer.py       # AI candidate scoring — builds prompt from job+applicant, parses JSON response (300s timeout)
  scheduler/
    jobs.py                # daily_summary_job — summarizes all groups, builds overview, cleanup
  api/
    routes.py              # GET /api/health, POST /api/bitrix/notify, POST /api/bitrix/broadcast (webhook endpoints)
      work.py              # Work day start logic (start_work_day callback handler with AI greeting)
      employee.py          # Employee search FSM + employee card display
data/
  arkadyjarvis.db          # SQLite database
  bitrix_tokens.json       # Bitrix OAuth tokens (auto-refreshed)
  .claude_token.json       # Claude OAuth tokens (auto-refreshed, single-use refresh tokens)
scripts/
  show_users.py            # CLI: all users + last activity (from message_buffer, 7-day window)
```

## Key Patterns

### Architecture
- **AIClient** wraps Claude CLI (`claude --print --output-format text`) as subprocess. Uses `CLAUDE_CODE_OAUTH_TOKEN` env var. Token auto-refreshed by `claude_token.py` before each call. Default 120s timeout, configurable per call. On timeout: `proc.kill()` + cleanup.
- **BitrixClient** is a singleton, refactored into package with mixins (`_base`, `_users`, `_calendar`, `_crm`, `_timeman`). File-based OAuth (`data/bitrix_tokens.json`), auto-refresh on expiry.
- **OpenRouterClient** is a singleton for image generation (Gemini 3 Pro via OpenRouter).
- **PotokClient** is a singleton for Potok.io ATS API (recruiter functionality).
- **JiraClient** uses a single integration user from settings: `async with JiraClient() as jira:`. Maps Telegram user to Jira reporter/assignee via Bitrix email lookup.
- All persistent state in SQLite via `app/db.py`.
- Services injected into dispatcher in `main.py` lifespan: `dp["ai_client"]`, `dp["bitrix"]`, `dp["openrouter"]`, `dp["openclaw"]`, `dp["potok"]`.
- **ErrorMiddleware** wraps all handlers — catches unhandled exceptions, logs them, replies with generic error.
- **AuthMiddleware** injects `db_user: dict` into every handler's kwargs. Checks muted groups before auth.
- Muted groups: bot collects messages for summarization but blocks all triggers (replies with rejection). Checked in AuthMiddleware before auth logic. CRUD: `db.is_group_muted()`, `db.add_muted_group()`, `db.remove_muted_group()`.

### Router Registration Order (in `create.py`)
Order matters — `buffer.py` must be last (catch-all):
1. start -> 2. summarize -> 3. meeting -> 4. free_slots -> 5. jira_task -> 6. lead -> 7. image -> 8. ask_ai -> 9. contract -> 10. employee -> 11. cicero -> 12. glafira -> 13. recruiter -> 14. group -> 15. buffer

### Authorization Flow
1. User sends `/start` -> bot looks up `@username` in Bitrix field (configured as `BITRIX_TELEGRAM_FIELD`, default `UF_USR_1678964886664`)
2. If found -> saves `(telegram_id, bitrix_user_id, display_name)` to `users` table
3. AuthMiddleware blocks protected commands if user not authorized
4. Public commands: `/start`, `/help` — always allowed without auth

### Interactive Menu Buttons (FSM)
- All MENU_KB buttons are interactive — clicking enters working mode via FSM states
- Summary button: in DM builds overview of all groups; in group summarizes current chat
- Meeting, Free slots, Task, Lead: set FSM state (`MeetingSetup.waiting_for_command`, `BookSlot.searching_attendee`, `CreateTask.waiting_for_input`, `CreateLead.waiting_for_info`) and wait for user input
- Image, Ask AI: enter FSM state and wait for prompt text
- Back menu button (`handle_back_menu`) calls `state.clear()` to exit any FSM state

### Free Slots + Booking (FSM)
- Entry: "Найди время" button -> FSM `BookSlot.searching_attendee` -> interactive search
- Computes free slots for 5 business days (9:00-19:00)
- Splits into hourly chunks, builds inline keyboard with slot buttons
- FSM states: `searching_attendee` -> `waiting_for_title` -> `waiting_for_slot` -> (`waiting_for_topic`)
- User picks slot -> types meeting title -> `BitrixClient.create_meeting()`
- Stale button handler (without StateFilter) shows alert "Кнопки устарели"
- Handler registration order critical: `handle_slot_selected` (with StateFilter) BEFORE `handle_stale_slot`

### Meeting Creation
- Entry: "Встреча" button -> FSM `MeetingSetup.waiting_for_command`
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
- Filters by DATE_FROM starting with today's date (Bitrix returns overlapping events including yesterday's)
- Displays as inline buttons with time + name, linking to Bitrix calendar event URL

### Image Generation
- Entry: "Картинка" button -> FSM `ImageGen.waiting_for_prompt` (text prompt or photo+caption)
- Uses `OpenRouterClient.generate_image()` via Gemini 3 Pro Image Preview
- Supports photo+caption mode: downloads photo, resizes to max 1024px, sends as base64 alongside prompt
- Handles multiple response formats from OpenRouter (images array, data URI in string, multimodal content array)

### Ask AI
- Entry: "Спроси AI" button -> FSM `AskAI.waiting_for_question`
- Uses `AIClient.complete()` (Claude CLI)
- Response converted via `md_to_telegram_html()` from `utils.py`

### Contract Check
- Entry: "Проверь договор" button -> FSM `ContractCheck.waiting_for_document`
- User uploads PDF/DOCX/TXT -> `document_parser.extract_text()` extracts plain text
- Prompt template loaded from `prompts/contract_check.md` via `prompts.load_prompt()`
- Prompt + text sent to `AIClient.complete(timeout=300)`
- Document text truncated to 120K chars to fit context
- Long responses split into 4000-char chunks (Telegram limit)
- Add new prompt-based assistants by dropping a `.md` file into `prompts/` and loading it via `load_prompt(name)`

### Cicero (Legal Consultant)
- Entry: "Цицерон" button -> FSM `Cicero.chatting` (persistent — multiple questions in a row)
- Accepts both plain text questions and documents (PDF/DOCX/TXT) with a caption
- System prompt from `prompts/cicero.md` (RU law consultant: ГК, КоАП, АПК, НК РФ, КонсультантПлюс)
- No conversation history — each question is standalone (prompt + question/document)
- Exits via "◀️ Меню" (`back:menu` callback clears FSM)

### Recruiter "Анатолий" (Potok.io Integration)
- **Potok.io** — ATS (Applicant Tracking System) for recruitment
- Access control: `RECRUITER_ALLOWED = {33570147, 367140321, 421632942}` (Artem, Natalya, Liza)
- Flow: hint:recruiter -> load jobs from Potok -> user picks job -> show description + candidate counts -> score new or rescore all
- FSM states: `Recruiter.choosing_job` -> `Recruiter.confirming` -> `Recruiter.scoring`
- **Candidate loading**: uses `/api/v3/jobs/{id}/ajs_joins.json` (cursor pagination) to get all applicant IDs for a job, then fetches details per applicant via `/api/v3/applicants/{id}.json` in parallel batches of 5 with 0.5s delay between batches (rate limit protection). Retry up to 3 times on 429 with `Retry-After` header.
- **Scoring**: `resume_scorer.py` builds detailed prompt (job desc + applicant resume/experience/skills) -> Claude returns JSON with score 0-100, breakdown by criteria, strengths, weaknesses. Uses 300s timeout.
- **Recruiter instructions**: job description can contain `"Важно для CLAUDE:"` section — extracted and injected as special instructions into the scoring prompt
- **Score push**: result posted as HTML comment to Potok event + applicant last_name prefixed with `{score:03d}-` for sorting (e.g., `085-Иванов`)
- **Skip scored**: candidates with `^\d{3}-` last_name prefix considered already scored
- **Telegram message limit**: scoring result text truncated to 4096 chars (Telegram max). Full result still goes to Potok comment.
- Stop button during scoring loop (`recruit:stop` callback)
- Job and applicant data cached in FSM after initial load (no duplicate fetches on score/rescore)
- Score labels: >=81 "Отлично", >=61 "Хорошо", >=41 "Средне", <41 "Слабо"

### Summarization
- `summarizer.py`: GPT prompt asks for HTML `<b>` tags
- `clean_html_for_telegram()` strips unsupported tags (`<br>`, `<p>`, `<div>`, etc.)
- Keeps only: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a>`
- Summary button in DM: builds overview of all group chats (not just current chat)

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

### Wednesday Frog Job (scheduler/jobs.py)
- Runs every Wednesday at 10:00 local time (timezone from settings)
- Skipped if `WEDNESDAY_FROG_CHAT_ID` is 0/unset
- Two-step pipeline:
  1. Claude CLI generates a fresh image prompt using `prompts/wednesday_frog.md` — different scene each week, always features a cartoon frog + the caption "Со средой, мои чуваки!"
  2. `OpenRouterClient.generate_image()` (Gemini 3 Pro Image) renders the picture
- Art style chosen randomly each week from `FROG_STYLES` list (65 recognizable styles) and injected into the prompt via `{style}` placeholder
- Sent via `bot.send_photo()` to the configured chat

### Monday Poster Job (scheduler/jobs.py)
- Runs every Monday at 09:00 local time
- Skipped if `MONDAY_POSTER_CHAT_ID` is 0/unset
- Generates a Soviet-1930s-style motivational poster (constructivism / Rodchenko / Klutsis / Lissitzky) with the caption "Наконец-то понедельник — и на любимую работу!" — worker hero varies each week (steelworker, pilot, kolkhoz worker, metrostroi builder, etc.)
- Prompt template: `prompts/monday_poster.md`

### Claude CLI (AI Client)
- AIClient calls `claude --print --output-format text` as subprocess
- Token: `CLAUDE_CODE_OAUTH_TOKEN` env var, auto-refreshed via `claude_token.py`
- `claude_token.py`: stores tokens in `data/.claude_token.json`, refresh tokens are single-use (rotate on each refresh), refreshes 10 min before expiry
- `init_token_file()` seeds from `CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_REFRESH_TOKEN` env vars on first run
- `ensure_fresh_token()` called before every CLI invocation
- Optional model override via `CLAUDE_MODEL` setting (e.g., `claude-opus-4-6`)
- On timeout: `proc.kill()` + `await proc.wait()` to prevent zombie processes

### OpenRouter
- Used for image generation (Gemini 3 Pro)
- API key: `OPENROUTER_API_KEY`
- `generate_image(prompt, image_b64?)` — returns raw PNG bytes

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
- OpenClaw workspace config: `~/.openclaw/workspace/` (SOUL.md, TOOLS.md, MEMORY.md)
- TOOLS.md contains site-specific hints (e.g., `ozon.ru` not `ozone.ru`, payment card selection)

### Potok.io API Details
- **API docs**: https://api-doc.potok.io/ (RapiDoc UI, OpenAPI spec at `potok-api-v3.yml`)
- **No server-side filtering**: `/api/v3/applicants` ignores all query params except `per_page` and `page`. Hard limit: 99 pages (9900 records). Sorting params ignored too.
- **Candidate loading by job**: Use `/api/v3/jobs/{job_id}/ajs_joins.json` (cursor-based pagination with `page_cursor` + `per_page`). Returns `objects[]` with `applicant_id`, `job_id`, `stage`, etc. No record limit.
- **Applicant details**: `/api/v3/applicants/{id}.json` — full profile with `ajs_joins`, `resumes`, `events`
- **Rate limiting**: 429 Too Many Requests on parallel batch requests. Keep batches ≤5, add 0.5s delay between batches, retry with `Retry-After` header.
- **Job details**: V2 (`/api/v2/jobs/{id}.json`) returns description in HTML; V3 returns more fields including `stages`, `applicants_count`
- **Score push**: `POST /api/v3/events.json` (comment) + `PATCH /api/v3/applicants/{id}.json` (last_name with score prefix)
- **Assessment cards API**: read-only, POST returns 404. Dynamic fields PATCH returns 200 but doesn't save.

## Database Schema (aiosqlite)

```sql
users (telegram_id PK, bitrix_user_id, bitrix_domain, display_name, is_active, created_at)
group_chats (chat_id PK, chat_title, added_at, summary_enabled)
message_buffer (id PK AUTO, chat_id, sender_id, sender_name, text, sent_at) + INDEX(chat_id, sent_at)
muted_groups (chat_id PK) — groups where bot collects messages but doesn't respond to triggers
```

## Config (.env)

Required: `BOT_TOKEN`

AI: `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_REFRESH_TOKEN` (for auto-refresh), `CLAUDE_CLI_PATH` (default `claude`), `CLAUDE_MODEL` (optional override), `CLAUDE_OAUTH_CLIENT_ID` (default official Claude Code ID)

OpenRouter: `OPENROUTER_API_KEY` (for image generation)

Bitrix24: `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `BITRIX_DOMAIN`, `BITRIX_REFRESH_TOKEN` (first run only), `BITRIX_TELEGRAM_FIELD` (default `UF_USR_1678964886664`)

Potok.io: `POTOK_API_TOKEN`, `POTOK_BASE_URL` (default `https://app.potok.io`)

OpenClaw: `OPENCLAW_URL`, `OPENCLAW_TOKEN`, `OPENCLAW_AGENT_ID` (default `main`)

Jira (integration user): `JIRA_URL`, `JIRA_USERNAME`, `JIRA_PASSWORD`

Webhook: `WEBHOOK_TOKEN` (shared secret for incoming B24 webhooks, header `X-Webhook-Token`)

Access control: `GLAFIRA_ALLOWED` (comma-separated Telegram IDs), `RECRUITER_ALLOWED` (comma-separated Telegram IDs)

Other: `DB_PATH` (default `data/arkadyjarvis.db`), `SUMMARY_HOUR` (default 19), `SUMMARY_MINUTE` (default 0), `TIMEZONE` (default `Asia/Novosibirsk`), `WEDNESDAY_FROG_CHAT_ID` (default 0 = disabled), `MONDAY_POSTER_CHAT_ID` (default 0 = disabled)

## Coding Guidelines

- **No hardcoded field IDs**: Bitrix24 custom fields (UF_*) must be in `config.py`, not in code. Field IDs are dynamic and opaque.
- **No hardcoded user IDs**: Access control lists (allowed users) must be in `.env`, not in code.
- **No hardcoded secrets**: All tokens, client IDs, secrets go in `.env` via pydantic-settings.
- **JSON from AI**: Use `utils.parse_json_response()` for parsing — handles markdown fences, embedded text. Don't duplicate parsing logic.
- **Timeman API**: `timeman.open` should NOT pre-fill `report` — reports are for `timeman.close` (end of day).
- **OpenClaw isolation**: Always pass `user_id` to `openclaw.stream_chat()` — each Telegram user gets isolated agent context via `x-openclaw-agent-id` header.
- **Lead creation**: Always include `SOURCE_ID`/`SOURCE_DESCRIPTION` and creator's Telegram contact in `COMMENTS` for traceability.
- **Callback handlers in start.py**: Don't rely on `db_user` from middleware kwargs — fetch via `db.get_user(callback.from_user.id)` directly.

## Running

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Health check: `curl localhost:8001/api/health`

Docker: `docker compose up --build` (exposes port 8002)

Logs in Docker: `docker compose logs -f`

## Known Issues

- Claude CLI requires `CLAUDE_CODE_OAUTH_TOKEN` — refresh tokens are single-use, lost token = re-auth needed
- Email guests cannot be created via Bitrix REST API (only UI)
- Bitrix OAuth tokens are shared (file-based), not per-user
- Email guest cache is in-memory (resets on restart)
- OpenRouter image generation may silently refuse due to content policy (0 completion tokens = refusal)
- Potok scored candidates identified by `^\d{3}-` last_name prefix — fragile convention
- Potok API SSL: uses Russian CA certificates — works from prod (Ubuntu), may fail from Mac without Russian CA bundle
- Tailscale + OpenVPN conflict: OpenVPN `redirect-gateway` kills Tailscale connectivity. Cannot run both simultaneously without server-side split tunnel config.
