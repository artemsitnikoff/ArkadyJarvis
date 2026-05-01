# ArkadyJarvis

Multi-user Telegram bot (BotFather, NOT userbot) for team chat summarization, Bitrix24 calendar/CRM, Jira integration, AI assistants (general, legal, recruiter, office-manager), image generation, recruiter scoring (Potok.io), contract validation, voice-to-lead, and scheduled motivational content.

## Tech Stack

- **Python 3.11+**, aiogram v3 (Telegram Bot API), FastAPI + Uvicorn
- **AI**: Claude CLI (subscription-based, no API tokens) via subprocess; OpenRouter (Gemini 3 Pro Image for image generation, Gemini 2.5 Pro for voice transcription)
- **Integrations**: Bitrix24 REST API, Jira REST API, Potok.io ATS API, OpenClaw (browser RPA via AI)
- Uvicorn owns the event loop; aiogram polling runs as `asyncio.create_task()` in FastAPI lifespan
- APScheduler for cron jobs (daily summary, Wednesday frog, Monday poster)
- aiosqlite for persistence (users, message buffer, group chats, muted groups)
- pydantic-settings for config from `.env`
- pypdf + python-docx for document parsing (contract check, Cicero)

## Project Structure

```
app/
  main.py                  # FastAPI app, lifespan, aiogram polling, APScheduler
  config.py                # pydantic-settings (Settings class, reads .env)
  db.py                    # aiosqlite: schema, CRUD (users, group_chats, message_buffer, muted_groups)
  utils.py                 # Parsers (time, attendees, Bitrix datetime), constants, merge_intervals, md_to_telegram_html(), parse_json_response()
  summarizer.py            # Claude summarization (group chat + daily overview)
  version.py               # __version__
  bot/
    create.py              # create_bot() + create_dispatcher() — router registration order matters
    middlewares.py         # ErrorMiddleware + AuthMiddleware (both handle Message and CallbackQuery)
    routers/
      start.py             # /start (auto-auth via @username -> Bitrix), /help, MENU_KB, hint callbacks, "Мои встречи", team, work:*
      summarize.py         # /summary command — on-demand chat summarization
      meeting.py           # FSM MeetingSetup — time/date/attendee parsing, Bitrix meeting creation
      free_slots.py        # FSM BookSlot — calendar accessibility + slot booking
      _attendee_picker.py  # Shared inline-keyboard helpers for meeting + free_slots attendee search
      jira_task.py         # FSM CreateTask — raw input reformatted via prompts/jira_task_template.md before ticket creation
      lead.py              # FSM CreateLead — text OR voice; voice transcribed via OpenRouter; AI extracts fields -> Bitrix CRM
      image.py             # FSM ImageGen — image generation via Gemini 3 Pro Image, supports photo+caption editing
      ask_ai.py            # FSM AskAI — Claude answers, md_to_telegram_html conversion
      contract.py          # FSM ContractCheck — parse PDF/DOCX/TXT, check against rules in prompts/contract_check.md
      employee.py          # FSM FindEmployee + employee card display
      cicero.py            # FSM Cicero — legal consultant (RU law), persistent chat with optional document attachments
      socrates.py          # FSM Socrates — meeting analyser (Yandex.Disk/direct URL → ffmpeg → transcript → review → expertise)
      glafira.py           # Glafira (AI office manager) — FSM chatting mode, OpenClaw streaming
      recruiter.py         # Марфа (AI recruiter) — Potok.io integration, candidate scoring via injected AIClient
      work.py              # Work day start logic (start_work_day callback handler with AI greeting)
      group.py             # on_bot_added / on_bot_removed — tracks group_chats in DB
      buffer.py            # Catch-all (LAST router): buffers all group messages to SQLite
  services/
    ai_client.py           # AIClient — Claude CLI wrapper (subprocess `claude --print`), configurable timeout (default 120s, 300s for scorer/contract/cicero)
    claude_token.py        # Claude OAuth token auto-refresh (file-based data/.claude_token.json), protected by asyncio.Lock
    bitrix_client/         # BitrixClient — refactored into package with mixins
      __init__.py           # BitrixClient class (combines all mixins)
      _base.py              # _BitrixBase — OAuth file-based tokens, HTTP client (timeout=30s), auto-refresh
      _calendar.py          # _BitrixCalendarMixin — calendar events, free slots, create_meeting, get_user_events (today only, filters declined/cancelled)
      _crm.py               # _BitrixCRMMixin — leads, CRM operations
      _timeman.py           # _BitrixTimemanMixin — work day start/status via timeman API
      _users.py             # _BitrixUsersMixin — user lookup, email guests, find_user_by_nickname, get_my_team
    jira_client.py         # JiraClient — async context manager (timeout=30s); retries create_issue without assignee on "cannot be assigned"
    document_parser.py     # Extract text from .pdf/.docx/.txt for contract check and Cicero
    ffmpeg_tool.py         # ffmpeg/ffprobe wrappers (convert_to_opus, probe_duration) — Socrates stage 0
    meeting_downloader.py  # Download recording from Yandex.Disk public API or direct URL
    meeting_pipeline.py    # Socrates orchestration: transcribe → review → expertise
    openclaw_client.py     # OpenClawClient — HTTP SSE client for OpenClaw gateway (per-user agent isolation via user_id)
    openrouter_client.py   # OpenRouterClient — image generation (Gemini 3 Pro Image) + voice transcription w/ diarization (Gemini 2.5 Pro)
    prompts.py             # load_prompt(name) — loads templates from prompts/ directory
    potok_client.py        # PotokClient — Potok.io ATS API (jobs, applicants via ajs_joins, scoring push)
    potok_models.py        # Pydantic models: Job, Applicant, Resume, CvParams, ScoringResult, ScoreBreakdown
    resume_scorer.py       # score_applicant(job, applicant, *, ai_client) — builds prompt, parses JSON, 300s timeout
  scheduler/
    jobs.py                # daily_summary_job, wednesday_frog_job, monday_poster_job (+ FROG_STYLES list)
  api/
    routes.py              # GET /api/health, POST /api/bitrix/notify, POST /api/bitrix/broadcast (webhook endpoints)
prompts/
  contract_check.md        # Contract validation checklist (company requisites, VAT, acceptance terms, etc.)
  cicero.md                # Legal consultant system prompt (ГК, КоАП, АПК, НК, КонсультантПлюс)
  jira_task_template.md    # Meta-prompt that reformats raw task description into structured ticket
  voice_transcribe.md      # Diarization prompt for voice messages
  wednesday_frog.md        # Meta-prompt for Wed 10:00 cartoon frog meme (with {style} placeholder)
  monday_poster.md         # Meta-prompt for Mon 09:00 Soviet-30s-style IT motivational poster
data/
  arkadyjarvis.db          # SQLite database
  bitrix_tokens.json       # Bitrix OAuth tokens (auto-refreshed)
  .claude_token.json       # Claude OAuth tokens (auto-refreshed, single-use refresh tokens)
scripts/
  show_users.py            # CLI: all users + last activity (from message_buffer, 7-day window)
  show_groups.py           # CLI: all group chats + 7-day message counts + mute/summary flags
  test_wednesday_frog.py   # Manually fire Wednesday frog for a given chat_id
  test_monday_poster.py    # Manually fire Monday poster for a given chat_id
```

## Key Patterns

### Architecture
- **AIClient** wraps Claude CLI (`claude --print --output-format text`) as subprocess. Uses `CLAUDE_CODE_OAUTH_TOKEN` env var. Token auto-refreshed by `claude_token.py` (serialised via `asyncio.Lock` — single-use refresh tokens must not race). Default 120s timeout, configurable per call. On timeout: `proc.kill()` + cleanup.
- **BitrixClient** is a singleton, refactored into package with mixins (`_base`, `_users`, `_calendar`, `_crm`, `_timeman`). File-based OAuth (`data/bitrix_tokens.json`), auto-refresh on expiry, `httpx.AsyncClient(timeout=30)`.
- **OpenRouterClient** is a singleton for image generation and voice transcription.
- **PotokClient** is a singleton for Potok.io ATS API (recruiter functionality).
- **JiraClient** uses a single integration user from settings: `async with JiraClient() as jira:`. Maps Telegram user to Jira reporter/assignee via Bitrix email lookup. `timeout=30`. Auto-retries `create_issue` without `assignee` if Jira returns 400 "cannot be assigned" — Jira then picks project default (usually project lead).
- All persistent state in SQLite via `app/db.py` (`buffer_message` commits on every INSERT to avoid data loss on crash).
- Services injected into dispatcher in `main.py` lifespan: `dp["ai_client"]`, `dp["bitrix"]`, `dp["openrouter"]`, `dp["openclaw"]`, `dp["potok"]`.
- **ErrorMiddleware** wraps `Message` and `CallbackQuery` handlers — catches unhandled exceptions, logs them, replies with generic error (via `message.reply` or `callback.answer(show_alert=True)`).
- **AuthMiddleware** injects `db_user: dict` into every handler's kwargs for both messages AND callbacks. For messages: checks muted groups, gates auth-required triggers. For callbacks: only injection, no gating (individual handlers enforce access via `GLAFIRA_ALLOWED`, `RECRUITER_ALLOWED`, etc.).
- Muted groups: bot collects messages for summarization but blocks responses. Checked in AuthMiddleware. CRUD: `db.is_group_muted()`, `db.add_muted_group()`, `db.remove_muted_group()`.

### Router Registration Order (in `create.py`)
Order matters — `buffer.py` must be last (catch-all):
1. start → 2. summarize → 3. meeting → 4. free_slots → 5. jira_task → 6. lead → 7. image → 8. ask_ai → 9. contract → 10. employee → 11. cicero → 12. socrates → 13. glafira → 14. recruiter → 15. group → 16. buffer

### Authorization Flow
1. User sends `/start` → bot looks up `@username` in Bitrix field (configured via `BITRIX_TELEGRAM_FIELD`, default `UF_USR_1678964886664`)
2. If found → saves `(telegram_id, bitrix_user_id, display_name)` to `users` table
3. AuthMiddleware blocks protected commands if user not authorized
4. Public commands: `/start`, `/help` — always allowed without auth
5. Callback handlers receive `db_user` via middleware; use it for auth-gated flows

### MENU_KB (Inline Keyboard)
Defined in `start.py`. Layout (rows top → bottom):
- Начать день в офисе
- Начать день удалённо
- ── separator ──
- Сотрудник | Моя команда
- Встреча | Найди время
- Задача | Лид
- Мои встречи | Картинка
- Спроси AI | Суммаризация
- Проверь договор | Цицерон
- Глафира | Марфа
- Все команды

Re-sent after every successful action. Imported by other routers: `from app.bot.routers.start import MENU_KB`. Every hint response includes `BACK_MENU_KB` ("◀️ Меню") for navigation back; `back:menu` callback calls `state.clear()`.

### Interactive Menu Buttons (FSM)
All MENU_KB buttons are interactive — clicking opens a working mode via FSM state. Button-only UX: there are no text regex triggers anymore (e.g. "создай встречу" / "нарисуй" / "ситников" — all removed). `/summary` slash command is the one exception, still works in groups.

### Meeting Creation
- Entry: "Встреча" button → FSM `MeetingSetup.waiting_for_command`
- `utils.parse_meeting_time()`: supports `HH:MM`, `HHMM`, `DD.MM`, `DD месяц`
- `utils.parse_attendees()`: emails removed from text BEFORE @nick extraction
- @nicks → `BitrixClient.find_user_by_nickname()` (Bitrix field `UF_USR_1678964886664`)
- Emails → `BitrixClient.resolve_email_user()`: user.get → email guest cache → description fallback
- User-supplied strings HTML-escaped before sending to Telegram

### Free Slots + Booking (FSM)
- Entry: "Найди время" button → FSM `BookSlot.searching_attendee` → interactive search
- Computes free slots for 5 business days (9:00-19:00)
- Splits into hourly chunks, builds inline keyboard with slot buttons
- FSM states: `searching_attendee` → `waiting_for_title` → `waiting_for_slot` → (`waiting_for_topic`)
- User picks slot → types meeting title → `BitrixClient.create_meeting()`
- Stale button handler (without StateFilter) shows alert "Кнопки устарели"
- Handler registration order critical: `handle_slot_selected` (with StateFilter) BEFORE `handle_stale_slot`

### Bitrix24 Email Guests
- `user.get` **excludes** email-type guests (documented Bitrix limitation)
- Email guests found via `im.user.list.get` — cached in `BitrixClient._email_guests_cache`
- Cannot create email guests via API, only through Bitrix UI

### My Meetings (Мои встречи)
- Button in MENU_KB → `hint:meetings` callback → `_show_meetings()` in `start.py`
- Fetches today's events via `bitrix.get_user_events(bitrix_user_id)`
- Filters: `DELETED != "Y"`, `DATE_FROM` starts with today, `MEETING_STATUS != "N"` (declined), `STATUS != "CANCELLED"`
- Logs every raw event's `MEETING_STATUS`/`STATUS`/`ACCESSIBILITY` for diagnostics
- Displays as inline buttons with time + name, linking to Bitrix calendar event URL

### Image Generation
- Entry: "Картинка" button → FSM `ImageGen.waiting_for_prompt` (text prompt or photo+caption)
- Uses `OpenRouterClient.generate_image()` via `google/gemini-3-pro-image-preview`
- Supports photo+caption mode: downloads photo, resizes to max 1024px, sends as base64 alongside prompt
- Handles multiple response formats from OpenRouter (images array, data URI in string, multimodal content array)

### Ask AI
- Entry: "Спроси AI" button → FSM `AskAI.waiting_for_question`
- Uses `AIClient.complete()` (Claude CLI)
- Response converted via `md_to_telegram_html()` from `utils.py`

### Contract Check
- Entry: "Проверь договор" button → FSM `ContractCheck.waiting_for_document`
- User uploads PDF/DOCX/TXT → `document_parser.extract_text()` extracts plain text
- Prompt template loaded from `prompts/contract_check.md` via `prompts.load_prompt()`
- Prompt + text sent to `AIClient.complete(timeout=300)`
- Document text truncated to 120K chars to fit context
- Short answers sent as HTML text; long answers sent as `.md` attachment with a short preview caption (avoids breaking HTML entities across chunks)

### Socrates (Meeting Analyser)
- Entry: "Сократ" button → FSM `Socrates.waiting_for_url` — user posts a URL to the recording
- Open to every authorised user. A per-user `asyncio.Lock` prevents one user from stacking parallel pipelines (each run spends Gemini + Claude ×2 + up to 1 GiB download)
- Every URL (original + Yandex-resolved + each redirect hop) passes an SSRF guard: DNS resolution + private-address blocklist (loopback / RFC1918 / link-local / CGNAT 100.64.0.0/10 / IPv6 ULA / IPv4-mapped IPv6). `follow_redirects=False` with a manual 5-hop loop re-validates every target
- Telegram bot uploads cap at 20 MB, so **only URLs are accepted**. Auto-resolved sources: Yandex.Disk public links (via `cloud-api.yandex.net`) and Google Drive share links (`/file/d/{ID}/view`, `?id={ID}` → rewritten to `drive.usercontent.google.com/download?...&confirm=t`, which bypasses the virus-scan warning page for public files). Direct HTTPS URLs work too.
- After redirects resolve, the response `Content-Type` is checked: if it starts with `text/html`, we abort with a readable error instead of letting ffmpeg choke on an HTML page (Drive viewer / "request access" stub / etc.)
- Stage 0: `meeting_downloader.download_meeting()` streams to a temp dir (ceiling 1 GiB) → `ffmpeg_tool.convert_to_opus()` produces mono 16 kHz opus @ 24 kbps → `probe_duration()` via ffprobe
- Meetings longer than `MEETING_MAX_MINUTES` (default 90) are rejected with a clear message — long recordings would overflow the OpenRouter base64 payload
- Stage 1: `OpenRouterClient.transcribe_voice()` → diarized markdown transcript
- Stage 2: Claude CLI with `prompts/meeting_review.md` → meeting review (protocol: decisions, next steps, open questions)
- Stage 3: Claude CLI with `prompts/meeting_brief.md` + transcript + review → analyst brief ("zero stage" prep: glossary, facts, vague wordings, strong quotes, 1–2-day domain onboarding plan, draft TOC of the future SOW, starter action list). Intentionally NOT an expert review — the human analyst does the judgement, AI just saves the first 2–4 hours of prep.
- All three artifacts are delivered as `.md` file attachments (`1_transcript.md`, `2_review.md`, `3_brief.md`)
- Temp directory (downloaded file + ogg) is wiped in `finally`
- `ffmpeg` is installed in the Docker image (apt package)

### Prompt files
- `contract_check.md` — contract validation checklist
- `cicero.md` — legal consultant system prompt
- `jira_task_template.md` — raw task description → structured Jira ticket
- `voice_transcribe.md` — diarization prompt (used by both Lead voice input and Socrates stage 1)
- `wednesday_frog.md` — Wed 10:00 meme generator
- `monday_poster.md` — Mon 09:00 constructivist IT poster
- `meeting_review.md` — Socrates stage 2 (review / protocol)
- `meeting_brief.md` — Socrates stage 3 (analyst brief: glossary, facts, onboarding plan, draft SOW TOC)

### Cicero (Legal Consultant)
- Entry: "Цицерон" button → FSM `Cicero.chatting` (persistent — multiple questions in a row)
- Accepts both plain text questions and documents (PDF/DOCX/TXT) with a caption
- System prompt from `prompts/cicero.md` (RU law consultant: ГК, КоАП, АПК, НК РФ, КонсультантПлюс)
- No conversation history — each question is standalone (prompt + question/document)
- Long answers attached as `.md` files, same as Contract Check
- Exits via "◀️ Меню" (`back:menu` callback clears FSM)

### Jira Task — AI Reformat
- Entry: "Задача" button → FSM `CreateTask.waiting_for_input`
- User types `DC <free-form description>` (DC = project key)
- Raw input reformatted via `prompts/jira_task_template.md` + Claude CLI → structured text (Задача / Приоритет / Контекст / Что сделать / Блокеры / Ожидаемый результат / Ориентир начала работ)
- Summary extracted from `**Задача:**` headline (line-by-line parse, strips markdown, collapses whitespace, caps 200 chars); falls back to first line of input
- Structured text becomes Jira `description`
- `jira_client.create_issue` retries without `assignee` if Jira returns 400 "cannot be assigned" → project lead is used

### Lead — Text or Voice
- Entry: "Лид" button → FSM `CreateLead.waiting_for_info`
- **Text**: AI extractor (`prompts/voice_transcribe.md` NOT used here; inline `EXTRACT_PROMPT` in `lead.py`) parses into NAME/LAST_NAME/COMPANY_TITLE/PHONE/EMAIL/COMMENTS
- **Voice** (Telegram voice, `F.voice`): downloads `.ogg` to temp file → `OpenRouterClient.transcribe_voice()` with diarization (prompts/voice_transcribe.md) → formatted `full_text` ("S1 [0:00]: ...") feeds the same text extractor → Bitrix lead
- Temp `.ogg` always deleted in `finally`
- `SOURCE_ID=OTHER`, `SOURCE_DESCRIPTION=Telegram-бот ArkadyJarvis`, creator's Telegram contact appended to `COMMENTS` for traceability
- Final reply HTML-escaped against `<`/`&`/`>` in names/companies

### Recruiter "Марфа" (Potok.io Integration)
- **Potok.io** — ATS (Applicant Tracking System) for recruitment
- Access control: `RECRUITER_ALLOWED` env var (comma-separated Telegram IDs)
- Flow: "Марфа" button → intro message → load jobs from Potok → user picks job → show description + candidate counts → score new or rescore all
- FSM states: `Recruiter.choosing_job` → `Recruiter.confirming` → `Recruiter.scoring`
- **Candidate loading**: uses `/api/v3/jobs/{id}/ajs_joins.json` (cursor pagination) to get all applicant IDs, then fetches details per applicant via `/api/v3/applicants/{id}.json` in parallel batches of 5 with 0.5s delay between batches (rate limit protection). Retry up to 3 times on 429 with `Retry-After`; raises `RuntimeError` if retries exhausted.
- **Scoring**: `resume_scorer.score_applicant(job, applicant, *, ai_client)` — takes the dispatcher-injected `AIClient`, builds prompt (job desc + applicant resume/experience/skills) → Claude returns JSON with score 0-100, breakdown, strengths, weaknesses. 300s timeout.
- **Recruiter instructions**: job description can contain `"Важно для CLAUDE:"` section — extracted and injected as special instructions into the scoring prompt
- **Score push**: result posted as HTML comment to Potok event + applicant last_name prefixed with `{score:03d}-` for sorting (e.g., `085-Иванов`)
- **Skip scored**: candidates with `^\d{3}-` last_name prefix considered already scored
- **Telegram message limit**: scoring result text truncated to 4096 chars (Telegram max). Full result still goes to Potok comment.
- Stop button during scoring loop (`recruit:stop` callback)
- Job and applicant data cached in FSM after initial load (no duplicate fetches on score/rescore)
- Score labels: >=81 "Отлично", >=61 "Хорошо", >=41 "Средне", <41 "Слабо"

### Summarization
- `summarizer.py`: Claude prompt asks for HTML `<b>`, `<i>`, `<code>` tags only (no markdown, no unsupported tags)
- Input truncated to `MAX_INPUT_CHARS = 100_000` (~25K tokens)
- Summary button in DM: `_run_summary()` in `start.py` fetches all groups the user belongs to (via `bot.get_chat_member`) and builds `daily_overview`
- Summary button in group: summarizes the current chat only
- `/summary` slash command: same as group button — summarizes current chat

### Daily Summary Job (scheduler/jobs.py)
- Runs at configured time (default 19:00 Novosibirsk)
- Summarizes each enabled group chat separately (summaries NOT sent to groups)
- Builds personalized daily overview per user: filters groups by membership via `bot.get_chat_member()`
- Sends overview **to each active user via DM** (not to group chats)
- `db.get_active_users()` returns all users with `is_active=1`
- Cleans up messages older than 7 days

### Wednesday Frog Job (scheduler/jobs.py)
- Runs every Wednesday at 10:00 local timezone
- Skipped if `WEDNESDAY_FROG_CHAT_ID` is 0/unset
- Pipeline:
  1. Random style picked from `FROG_STYLES` (65 recognizable styles: Picasso, Van Gogh, anime, Ghibli, Pollock, noir, pixel-art, synthwave, Moebius, etc.) and injected into `prompts/wednesday_frog.md` via `{style}` placeholder
  2. Claude CLI renders a fresh scene around a cartoon frog + caption "Со средой, мои чуваки!"
  3. `OpenRouterClient.generate_image()` (Gemini 3 Pro Image) renders the picture
- Sent via `bot.send_photo()`; caption shows which style was used
- Manual test: `scripts/test_wednesday_frog.py [chat_id]` (default -790607108)

### Monday Poster Job (scheduler/jobs.py)
- Runs every Monday at 09:00 local timezone
- Skipped if `MONDAY_POSTER_CHAT_ID` is 0/unset
- Generates a constructivist 1930s-style motivational poster (Rodchenko/Lissitzky/Klutsis visual language — red/black/white palette, diagonals, photomontage — **without** Soviet iconography) with caption "Наконец-то понедельник — и на любимую работу!"
- Hero is an IT-specialist each week (developer, DevOps, SRE, QA, PM, analyst, designer, tech lead, data scientist, etc.)
- Prompt template: `prompts/monday_poster.md`
- Manual test: `scripts/test_monday_poster.py [chat_id]` (default -790607108)

### Claude CLI (AI Client)
- `AIClient.complete(prompt, timeout=120)` → calls `claude --print --output-format text` as subprocess
- argv logged before each call (so `--model claude-opus-4-7` is visible in server.log)
- Token: `CLAUDE_CODE_OAUTH_TOKEN` env var, auto-refreshed via `claude_token.py`
- `claude_token.py`: stores tokens in `data/.claude_token.json`, refresh tokens are single-use, refreshes 10 min before expiry
- `ensure_fresh_token()` is protected by module-level `asyncio.Lock` — parallel callers wait on the lock; second caller re-reads the file and skips refresh if another task already rotated the token
- `init_token_file()` seeds from `CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_REFRESH_TOKEN` env vars on first run
- Optional model override via `CLAUDE_MODEL` setting (e.g., `claude-opus-4-7`)
- On timeout: `proc.kill()` + `await proc.wait()` to prevent zombie processes

### OpenRouter
- Used for image generation (Gemini 3 Pro Image) and voice transcription (Gemini 2.5 Pro)
- API key: `OPENROUTER_API_KEY`, text/audio model: `OPENROUTER_MODEL` (default `google/gemini-2.5-pro`)
- `generate_image(prompt, image_b64?)` — returns raw PNG bytes
- `transcribe_voice(ogg_path)` — returns `TranscriptionResult` (success flag, speakers_count, segments with start/end/speaker/text, formatted `full_text` like `S1 [0:00]: …`). Used in Lead router for voice input.

### Glafira (AI Office Manager via OpenClaw)
- **OpenClaw** — AI agent that controls browser via prompts (RPA), installed on Mac
- Mac (OpenClaw gateway): Tailscale IP `100.96.205.95:18789`, bind `lan` (0.0.0.0)
- Ubuntu server (Jarvis prod): Tailscale IP `100.109.25.60`
- Gateway auth: token-based (`OPENCLAW_TOKEN`), HTTP endpoint `/v1/chat/completions`
- **OpenClawClient** (`app/services/openclaw_client.py`): HTTP SSE streaming via httpx, `stream_chat(messages)` yields text chunks
- **Glafira router**: FSM state `Glafira.chatting`, persistent conversation mode (FSM not cleared after each response)
- Access control: `GLAFIRA_ALLOWED` env var (comma-separated Telegram IDs)
- Streaming UX: sends "Думаю...", edits it as chunks arrive (throttled: 0.8s between edits, min 20 new chars), `html.escape()` on content
- Stream exceptions narrow: `TelegramBadRequest` ("not modified" ignored, others logged), `TelegramRetryAfter` backs off
- Exit via `glafira:exit` callback to properly clear FSM state
- Conversation history stored in FSM data, capped at 20 messages
- OpenClaw model: Claude Sonnet 4.6 via OpenRouter
- OpenClaw workspace config: `~/.openclaw/workspace/` (SOUL.md, TOOLS.md, MEMORY.md)

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

AI: `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_REFRESH_TOKEN` (for auto-refresh), `CLAUDE_CLI_PATH` (default `claude`), `CLAUDE_MODEL` (optional override, e.g. `claude-opus-4-7`), `CLAUDE_OAUTH_CLIENT_ID` (default official Claude Code ID)

OpenRouter: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (default `google/gemini-2.5-pro` — used for voice transcription), `OPENROUTER_TIMEOUT` (default 300s, read/write; connect is always 10s)

Bitrix24: `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `BITRIX_DOMAIN`, `BITRIX_REFRESH_TOKEN` (first run only), `BITRIX_TELEGRAM_FIELD` (default `UF_USR_1678964886664`), `BITRIX_EMAIL_GUESTS_SCAN_MAX` (default 2000), `BITRIX_EMAIL_GUESTS_MULTIPLIER` (default 3)

Potok.io: `POTOK_API_TOKEN`, `POTOK_BASE_URL` (default `https://app.potok.io`)

OpenClaw: `OPENCLAW_URL`, `OPENCLAW_TOKEN`, `OPENCLAW_AGENT_ID` (default `main`)

Jira (integration user): `JIRA_URL`, `JIRA_USERNAME`, `JIRA_PASSWORD`

Webhook: `WEBHOOK_TOKEN` (shared secret for incoming B24 webhooks, header `X-Webhook-Token`)

Access control: `GLAFIRA_ALLOWED` (comma-separated Telegram IDs), `RECRUITER_ALLOWED` (comma-separated Telegram IDs)

Scheduled content: `WEDNESDAY_FROG_CHAT_ID` (default 0 = disabled), `MONDAY_POSTER_CHAT_ID` (default 0 = disabled)

Socrates: `FFMPEG_BIN` (default `ffmpeg`), `MEETING_MAX_MINUTES` (default 90)

Other: `DB_PATH` (default `data/arkadyjarvis.db`), `SUMMARY_HOUR` (default 19), `SUMMARY_MINUTE` (default 0), `TIMEZONE` (default `Asia/Novosibirsk`)

## Coding Guidelines

- **No hardcoded field IDs**: Bitrix24 custom fields (UF_*) must be in `config.py`, not in code. Field IDs are dynamic and opaque.
- **No hardcoded user IDs**: Access control lists (allowed users) must be in `.env`, not in code.
- **No hardcoded secrets**: All tokens, client IDs, secrets go in `.env` via pydantic-settings.
- **JSON from AI**: Use `utils.parse_json_response()` for parsing — handles markdown fences, embedded text. Don't duplicate parsing logic.
- **Timeman API**: `timeman.open` should NOT pre-fill `report` — reports are for `timeman.close` (end of day).
- **OpenClaw isolation**: Always pass `user_id` to `openclaw.stream_chat()` — each Telegram user gets isolated agent context via `x-openclaw-agent-id` header.
- **Lead creation**: Always include `SOURCE_ID`/`SOURCE_DESCRIPTION` and creator's Telegram contact in `COMMENTS` for traceability.
- **HTML-escape user strings**: When building HTML messages (default parse_mode), always `html.escape()` user-controlled strings (names, companies, titles, AI-returned free text). Broken entities → Telegram 400.
- **Long AI answers** (>4000 chars): attach as `.md` file with a short preview caption instead of trying to chunk HTML.
- **AIClient injection**: services that need Claude (e.g. `resume_scorer.score_applicant`) must receive `ai_client` as parameter, not instantiate new `AIClient()`.
- **Db user in callbacks**: `AuthMiddleware` injects `db_user` into callback handlers — use the injected value instead of re-fetching via `db.get_user(...)`. Some older callbacks still re-fetch (meeting.py, free_slots.py) — cleanup pending, not a bug.
- **Prompts live in `prompts/`**: add new assistants by dropping a `.md` file and loading via `load_prompt(name)`. Template placeholders (`{style}`) are substituted by the caller.

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
- Email guest cache is in-memory (resets on restart); `_load_email_guests` throttles 0.3s between batches and leaves the cache unloaded for retry if every batch fails
- OpenRouter image generation may silently refuse due to content policy (0 completion tokens = refusal)
- Potok scored candidates identified by `^\d{3}-` last_name prefix — fragile convention
- Potok API SSL: uses Russian CA certificates — works from prod (Ubuntu), may fail from Mac without Russian CA bundle
- Tailscale + OpenVPN conflict: OpenVPN `redirect-gateway` kills Tailscale connectivity. Cannot run both simultaneously without server-side split tunnel config.
- Some callback handlers (`meeting.py`, `free_slots.py`) still re-fetch `db_user` via `db.get_user()` instead of using the middleware-injected one — not broken, just duplicated DB calls
- Socrates SSRF guard has a narrow TOCTOU DNS-rebinding window: `_assert_public_url` resolves the host and then httpx resolves it again on connect. Fully closing this needs a pinned-IP transport (custom httpcore pool). Compensating controls: authorised users only (Bitrix-tied `/start` auth) + internal Tailscale-only deployment.
