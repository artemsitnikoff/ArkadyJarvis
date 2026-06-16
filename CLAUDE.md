# ArkadyJarvis

Multi-user Telegram bot (BotFather, NOT userbot) для команды Digital Clouds: суммаризация чатов, Bitrix24 calendar/CRM, Jira, AI-ассистенты (общий, юрист, рекрутёр, офис-менеджер, бизнес-разведка), генерация изображений, скоринг резюме (Potok.io), проверка договоров, voice-to-lead, аналитика отдела продаж, мониторинг Zabbix, регулярный мотивационный контент.

## Tech Stack

- **Python 3.11+**, aiogram v3 (Telegram Bot API), FastAPI + Uvicorn
- **AI**:
  - Claude CLI (subscription, `--print` subprocess) для всех текстовых задач — Sonnet по-умолчанию, Haiku для дешёвых классификаторов (`model="claude-haiku-4-5-20251001"`)
  - OpenRouter — **только** аудио/видео: Gemini 3 Pro Image (генерация), Gemini 2.5 Pro (транскрипция voice / mp3 со звонков)
- **Userbot**: Telethon (StringSession) для чтения истории каналов (Zabbix backfill), отправки сообщений кандидатам с личного аккаунта рекрутёра
- **Integrations**: Bitrix24 REST + Bitrix calendar-sharing короткие ссылки; Jira REST; Potok.io ATS (REST API + frontend `/client_api/*` через cookies для HH-messaging); OpenClaw (browser RPA); DaData (карточки ЮЛ по ИНН, бесплатный API key); ГИР БО ФНС (`bo.nalog.gov.ru` — бухотчётность, без авторизации); SBIS/Saby — рассматривали для разведки, отказались (нужна платная VOK-лицензия)
- Uvicorn owns the event loop; aiogram polling запускается как `asyncio.create_task()` в FastAPI lifespan
- APScheduler — все cron-задачи (daily summary, frog, poster, zabbix check, sales dept summary)
- aiosqlite — все persistent state
- pydantic-settings — `.env`
- pypdf + python-docx — извлечение текста для contract check / Cicero

## Project Structure

```
app/
  main.py                  # FastAPI app, lifespan, polling, scheduler, инжекция сервисов
  config.py                # pydantic-settings Settings — все ENV
  db.py                    # aiosqlite schema + CRUD (users, group_chats, message_buffer, muted_groups, recruiter_contacts, zabbix_problems)
  utils.py                 # parse_meeting_time/attendees, md_to_telegram_html, parse_json_response, merge_intervals
  summarizer.py            # Claude-суммаризация чатов и daily overview
  version.py               # __version__
  bot/
    create.py              # create_bot() + create_dispatcher() — порядок роутеров КРИТИЧЕН
    middlewares.py         # ErrorMiddleware + AuthMiddleware (Message + CallbackQuery)
    routers/
      start.py             # /start (Bitrix-auth по @username), /help, MENU_KB, hint:* dispatcher (включая Stirlitz/Recruiter/Glafira), команда, мои встречи
      summarize.py         # /summary slash-команда
      meeting.py           # FSM MeetingSetup — встречи в Bitrix
      free_slots.py        # FSM BookSlot — поиск слотов + бронирование
      _attendee_picker.py  # Общий attendee-search для meeting/free_slots
      jira_task.py         # FSM CreateTask — Claude reformat → Jira issue
      lead.py              # FSM CreateLead (текст/voice через OpenRouter → Bitrix CRM lead)
      image.py             # FSM ImageGen — Gemini 3 Pro Image (текст или photo+caption)
      ask_ai.py            # FSM AskAI — Claude с персонажем «Джарвис Аркадия» (prompts/ask_ai_system.md)
      contract.py          # FSM ContractCheck — PDF/DOCX/TXT → Claude по prompts/contract_check.md
      employee.py          # FSM FindEmployee + карточка сотрудника
      cicero.py            # FSM Cicero — юрист по RU праву (persistent chat)
      socrates.py          # FSM Socrates — анализ записи встречи (URL→ffmpeg→Gemini→Claude×2)
      stirlitz.py          # FSM Stirlitz — разведка по компании/человеку (DaData + ГИР БО + WebSearch)
      glafira.py           # «Марфа» (AI офис-менеджер) — OpenClaw streaming. ВНИМАНИЕ: persona в UI = «Марфа», файл/класс остался Glafira
      recruiter.py         # «Глафира» (AI рекрутёр) — Potok.io скоринг + Telethon-рассылка + HH-fallback. Persona UI = «Глафира», класс = Recruiter
      zabbix.py            # channel_post handler для Zabbix-канала → SQLite
      work.py              # work:* callback (dead code — кнопки удалены, файл оставлен)
      group.py             # on_bot_added/removed — track group_chats
      buffer.py            # Catch-all (LAST!) — буферизация всех group messages
  services/
    ai_client.py           # AIClient — Claude CLI wrapper (subprocess). Параметры: prompt, timeout, system_prompt, allowed_tools, model
    claude_token.py        # Auto-refresh CLAUDE_CODE_OAUTH_TOKEN (data/.claude_token.json, asyncio.Lock — single-use refresh)
    bitrix_client/         # BitrixClient — пакет с миксинами
      __init__.py           # композиция миксинов
      _base.py              # _BitrixBase — OAuth file-based, httpx(timeout=30), auto-refresh
      _calendar.py          # _BitrixCalendarMixin — события, free slots, create_meeting, get_user_events
      _crm.py               # _BitrixCRMMixin — лиды, операции CRM
      _timeman.py           # _BitrixTimemanMixin — timeman API (start of work day — кнопки убраны но API живой)
      _users.py             # _BitrixUsersMixin — user.get, email guests, find_user_by_nickname, get_my_team
    jira_client.py         # JiraClient — async context manager. Auto-retries без assignee
    document_parser.py     # Извлечение текста из PDF/DOCX/TXT
    ffmpeg_tool.py         # convert_to_opus, probe_duration — Socrates stage 0
    meeting_downloader.py  # Скачивание из Yandex.Disk / Google Drive / прямой URL
    meeting_pipeline.py    # Socrates orchestration
    openclaw_client.py     # OpenClawClient — HTTP SSE, per-user agent isolation
    openrouter_client.py   # OpenRouterClient — generate_image + transcribe_voice(format="ogg"|"mp3"|…)
    prompts.py             # load_prompt(name) — чтение prompts/<name>.md
    potok_client.py        # PotokClient — Potok.io REST (Bearer-токен): jobs, applicants, scoring push, stage move, кэш questions, post comments
    potok_frontend.py      # PotokFrontendClient — frontend /client_api/* через DeviseTokenAuth (3 cookie/header) — отправка HH-сообщений
    potok_models.py        # Pydantic: Job, Applicant (+ accounts), Resume, CvParams, AjsJoin (+ state_id), ScoringResult, ScoreBreakdown
    resume_scorer.py       # score_applicant(job, applicant, *, ai_client) — Claude scoring + questions
    rejection_classifier.py # classify_rejection_intent(text, ai_client) — Haiku scoring (0-100) ответа кандидата на «отказ»
    userbot.py             # UserbotClient (Telethon) — send_to_user, resolve_phone (ImportContactsRequest), on_incoming hook
    dadata_client.py       # DaDataClient — find_by_id (по ИНН), suggest (по названию)
    giro_client.py         # GiroClient — bo.nalog.gov.ru: search org, fetch bfo (выручка/активы по годам)
    stirlitz.py            # Orchestrator: classify_intent (Haiku) → company_inn|company_name|person|clarify → DaData+GIRO или WebSearch
    sales_analytics.py     # DailySalesActivity + collect_user_activity (Bitrix metrics + voximplant calls + транскрипция через openrouter+ai_client)
    zabbix_monitor.py      # parse_zabbix_message (regex по 🔴/🟢), check_unresolved_and_create_jira
  scheduler/
    jobs.py                # daily_summary_job, wednesday_frog_job, monday_poster_job, sales_dept_summary_job, zabbix_check_unresolved_job
  api/
    routes.py              # GET /api/health, POST /api/bitrix/notify, POST /api/bitrix/broadcast
prompts/
  contract_check.md        # Чек-лист проверки договора
  cicero.md                # Юрист-консультант (ГК, КоАП, АПК, НК)
  jira_task_template.md    # Reformat задачи под наш шаблон
  voice_transcribe.md      # Diarization (Lead voice + Socrates stage 1)
  wednesday_frog.md        # Мем-лягушка с {style}
  monday_poster.md         # Constructivist IT-плакат
  meeting_review.md        # Socrates stage 2
  meeting_brief.md         # Socrates stage 3
  ask_ai_system.md         # Персонаж «Джарвис Аркадия» для AskAI
  digital_clouds_context.md # SHARED — описание DC (4 юнита, цели 2026, проблемы) для всех sales-/recon-промптов
  stirlitz.md              # Card компании по DaData+ГИР БО (вызывает WebSearch)
  stirlitz_person.md       # Recon человека через WebSearch (LinkedIn, Habr, VK)
  stirlitz_intent.md       # Haiku-диспетчер: company_inn|company_name|person|clarify (JSON)
  rejection_classifier.md  # Haiku-классификатор отказа в ответе кандидата (0-100)
  sales_summary.md         # Общий отчёт по продажнику (с DC-контекстом, «играющий РОП»-укол)
  sales_call_analysis.md   # Per-call разбор «📝 Суть / ✅ Хорошо / ⚠️ Улучшить» (с правилами фильтрации спам-входящих и игнорирования ASR-артефактов)
data/
  arkadyjarvis.db          # SQLite database
  bitrix_tokens.json       # Bitrix OAuth (auto-refreshed)
  .claude_token.json       # Claude OAuth (auto-refreshed, single-use refresh)
scripts/
  show_users.py            # CLI users + activity (7d)
  show_groups.py           # CLI group_chats counts
  test_wednesday_frog.py   # Manual Wed frog
  test_monday_poster.py    # Manual Mon poster
  create_userbot_session.py # Сгенерировать TELETHON_SESSION (StringSession) — разовый интерактив
  scan_zabbix_month.py     # Backfill Zabbix history за 30 дней через userbot → создание Jira-задач
  test_potok_events.py     # Дамп Potok events на applicant (для отладки questions marker)
  test_potok_move_stage.py # Brute-force подбора endpoint смены стадии в Potok (исторический)
  test_potok_communicate_frontend.py # Тест отправки HH через /client_api/communicate (с auto-extract channels)
  test_potok_hh_messaging.py # Discovery — какие endpoints доступны для HH
  test_potok_communicate.py # Те же endpoints через публичный Bearer (для проверки что не пускают)
  test_sbis_auth.py        # SBIS/Saby — discovery interactive login (исторический, отказались)
  test_sbis_partner.py     # SBIS partner spp-rest-api проверка (исторический)
  test_rejection_classifier.py # Прогон rejection LLM на встроенных кейсах
  test_sales_report.py     # Полный sales report end-to-end — печать + опц. отправка всем из SALES_REPORT_RECIPIENTS (--no-send для dry-run)
  list_user_leads.py       # Аудит активных лидов менеджера по статусам
  inspect_applicant.py     # Поиск кандидата по имени во ВСЕХ вакансиях (для «ghost» candidates)
  inspect_hh_channels.py   # Расшифровка accounts[].url ?t=<channel> у HH-кандидатов
```

## Key Patterns

### Architecture

- **AIClient** (`services/ai_client.py`) — обёртка над `claude --print --output-format text`. Параметры:
  - `prompt` — текст
  - `timeout` (default 120)
  - `system_prompt` — добавляется через `--append-system-prompt` (только AskAI его использует)
  - `allowed_tools` — comma-separated. Используется выборочно для read-only tools (`WebSearch,WebFetch` в Штирлице). Изменяет `--disallowed-tools` исключая их из ban-list.
  - `model` — override `settings.claude_model` per-call. Hint классификаторы передают `"claude-haiku-4-5-20251001"`.
  - **Security**: subprocess запускается с `cwd="/tmp"` — иначе CLI подхватывает project CLAUDE.md как system context и считает все вопросы «вне темы».
  - **Security**: `--disallowed-tools` блокирует `Bash, BashOutput, KillShell, Read, Write, Edit, MultiEdit, NotebookEdit, Glob, Grep, WebFetch, WebSearch, Task, Agent, SlashCommand, TodoWrite, ExitPlanMode` — был critical RCE bug когда пользователь писал «сделай cd, ls» и CLI реально запускал шелл.
- **BitrixClient** — singleton, миксины (`_base`, `_users`, `_calendar`, `_crm`, `_timeman`). File-based OAuth (`data/bitrix_tokens.json`), auto-refresh.
- **OpenRouterClient** — singleton. `generate_image(prompt, image_b64?)`, `transcribe_voice(path, audio_format="ogg")` — формат настраивается (mp3 для записей звонков).
- **PotokClient** — singleton, Bearer-токен через `POTOK_API_TOKEN`. In-memory cache вопросов (`_questions_cache`) — заполняется при `push_scoring`, читается при отправке вопросов в Telegram.
- **PotokFrontendClient** — отдельный singleton для `/client_api/*` (HH-messaging). Auth через 3 заголовка DeviseTokenAuth (`access-token`, `client`, `uid`). Токены статичны (не ротируются), TTL ~5 месяцев. Получаются один раз из браузерной сессии (DevTools → Network → любой XHR на online.sbis... извини, на app.potok.io).
- **UserbotClient** (Telethon) — `send_to_user(user_id, text)`, `resolve_phone(phone)` через `ImportContactsRequest` (+ автоматический cleanup). Слушает `events.NewMessage(incoming=True)`. В `main.py` регистрируется callback `_on_candidate_reply` — при входящем от tg_id из `recruiter_contacts` сообщение сохраняется в Potok + классифицируется на «отказ» через `rejection_classifier`. Если score > порог — `potok.set_applicant_active(active=False)` + audit-комментарий.
  - **Пасхалка «ситников»** (`enable_seneca(ai_client)`, включается в `main.py` после старта): на входящее сообщение со словом «ситников» личный аккаунт отвечает «Аве, Цезарь!» + случайной цитатой Сенеки (Haiku). Кулдаун `SENECA_COOLDOWN_SECONDS=60` на чат — чтобы личный аккаунт не словил флуд-бан. Намеренно в **userbot** (ответ «под юзером»), не в боте. Работает только при живой Telethon-сессии.
- **JiraClient** — async context manager. Auto-retry без assignee.
- **DaDataClient** — `find_by_id(inn)` (точный поиск), `suggest(query)` (свободный поиск). 10k запросов/сутки бесплатно.
- **GiroClient** — публичный API `bo.nalog.gov.ru`, без авторизации. `get_summary(inn)` → выручка/активы по годам.
- Все сервисы инжектятся в dispatcher в lifespan: `dp["ai_client"]`, `dp["bitrix"]`, `dp["openrouter"]`, `dp["openclaw"]`, `dp["potok"]`, `dp["potok_frontend"]`, `dp["dadata"]`, `dp["giro"]`, `dp["userbot"]`.
- **ErrorMiddleware** + **AuthMiddleware** на Message и CallbackQuery (порядок: error wraps auth wraps handler).
- **AuthMiddleware** инжектит `db_user` во ВСЕ хендлеры; gating для protected команд только в message-mode.

### Router Registration Order (in `create.py`)

Order matters — `buffer.py` ВСЕГДА последний (catch-all):

1. start → 2. summarize → 3. meeting → 4. free_slots → 5. jira_task → 6. lead → 7. image → 8. ask_ai → 9. contract → 10. employee → 11. cicero → 12. socrates → 13. glafira → 14. recruiter → 15. stirlitz → 16. hudson → 17. zabbix → 18. group → 19. **buffer**

⚠️ ВАЖНО про `hint:*` callbacks: общий обработчик `F.data.startswith("hint:")` в `start.py` ловит ВСЕ hint-клики первым. Узкие хендлеры в роутерах-фичах перебить его НЕ могут. Любая новая кнопка-открыватель FSM должна быть зарегистрирована в `_simple_fsm_hints()` внутри `start.py`.

### Personas naming (исторический своп)

- `recruiter.py` (Potok.io scoring) → отображается как **«Глафира»** в UI (👔)
- `glafira.py` (OpenClaw office-manager) → отображается как **«Марфа»** в UI (🤖)
- Файлы/классы/callback-ID **НЕ переименованы** во избежание ломки. Только display strings в `start.py` и сообщениях.

### Authorization Flow

1. `/start` → Bitrix lookup `@username` (поле `BITRIX_TELEGRAM_FIELD`, default `UF_USR_1678964886664`)
2. Найдено → `(telegram_id, bitrix_user_id, display_name)` в `users`
3. AuthMiddleware блокирует protected для unauth users
4. Public: `/start`, `/help`

### MENU_KB (Inline Keyboard)

Defined в `start.py`. Текущая раскладка:
- Сотрудник | Моя команда
- Встреча | Найди время
- Задача | Лид
- Мои встречи | Картинка
- Спроси AI | Суммаризация
- Проверь договор | Цицерон
- 🎓 Сократ | 🕵️ Штирлиц
- 🤖 Марфа | 👔 Глафира
- Все команды

«Начать день в офисе/удалённо» **удалены** (команда использует Bitrix24 check-in вместо них).

### Ask AI

- Entry: «Спроси AI» → FSM `AskAI.waiting_for_question`
- `prompts/ask_ai_system.md` — персонаж «Джарвис Аркадия» (Digital Clouds), универсальный помощник
- Передаётся через `--append-system-prompt`
- Без agentic поведения (все tools заблокированы)

### Stirlitz (B2B разведка)

- Entry: 🕵️ Штирлиц → FSM `Stirlitz.waiting_for_query`
- **Diapatcher** через Haiku (`prompts/stirlitz_intent.md`): пользовательский запрос (история до 6 сообщений) → JSON `{kind: company_inn|company_name|person|clarify, ...}`
- **`company_*`**: `DaData.find_by_id`/`suggest` + `GiroClient.get_summary` → JSON → Claude (Sonnet) с `prompts/stirlitz.md` + DC-контекстом + **WebSearch** для свежих новостей/тендеров. allowed_tools="WebSearch,WebFetch"
- **`person`**: Claude (Sonnet) с `prompts/stirlitz_person.md` + WebSearch — ищет LinkedIn, Habr, VK, конференции
- **`clarify`**: FSM остаётся в waiting_for_query, бот задаёт вопрос → пользователь уточняет → Haiku видит обе реплики
- Краткие карточки → в чат; длинные (>4000 символов) → .md attachment

### Sales Department Analytics

Полный аудит активности продажника из Bitrix24, на двух cron-расписаниях.

**Cron:**
- Дневной 19:00 (`SUMMARY_HOUR`), **только пн-пт** (`day_of_week="mon-fri"`)
- Недельный пятница 18:00

**Адресаты:** `SALES_REPORT_RECIPIENTS` (Telegram IDs через запятую; группы — с минусом, например `-4729014928`). Бот должен быть участником группы.

**Метрики (`sales_analytics.collect_user_activity`):**
- **Лиды**: `created` (за период), `active` — фильтр `!STATUS_SEMANTIC_ID IN [S, F]` (Bitrix сам определяет «в работе» через семантику статусов)
- **Сделки**: `created`/`active`/`modified`/`won` (+`won_sum`)/`hot` (+`hot_sum`)/`avg_deal_age_days`
  - `deals_active` = все open в разрешённых воронках (`SALES_REPORT_DEAL_CATEGORIES`, default `27,31,33` — Услуги Б24, Общая, ПиК; исключает «Счета 1С» cat 0, «Продление Битрикс» cat 29, «Квал» cat 23 — там «фантомные» сделки менеджера)
  - `deals_hot` = subset где имя стадии совпадает с `SALES_REPORT_ACTIVE_DEAL_PATTERNS` (default `кп,договор,счёт,счет,переговор,согласи,кэв провед,отработк,оплат`). Имена стадий тянутся через `crm.dealcategory.stage.list`
- **План/факт за календарный месяц**: `month_won_sum` + `monthly_plan` (env `SALES_REPORT_MONTHLY_PLAN`, default 220000₽). WON считается через `STAGE_SEMANTIC_ID=S` + `>=CLOSEDATE start_of_month`
- **Дела**: `activities_done` (звонки/встречи/задачи через `crm.activity.list`), `stage_changes` через `crm.stagehistory.list` — N+1 fetch по каждой сделке менеджера (фильтра по user в API нет, только OWNER_ID=deal_id), параллелится sem(5)
- **Комментарии**: `crm.timeline.comment.list` (AUTHOR_ID=user_id)
- **Звонки** (voximplant.statistic.get):
  - Раздельно in/out/missed/callback по `CALL_TYPE` (1/2/3/4)
  - `entity_type`/`entity_name` резолвится через `crm.lead.get|contact.get|company.get|deal.get` (cached в `_enrich_calls`)
  - **Транскрипция** (если `with_transcripts=True`): скачиваем mp3 с Bitrix Disk через `disk.file.get` → Gemini 2.5 Pro `transcribe_voice(audio_format="mp3")` → Sonnet анализ через `prompts/sales_call_analysis.md` (с DC-контекстом и правилами фильтрации: не считать поставщиков воды нашими лидами, игнорить ASR-артефакты в произношении email и т.п.)
  - Параллельно sem(3), лимит max_transcripts=25 (топ по длительности)
  - Звонки <15 сек без транскрипции (короткие гудки)

**AI-отчёт** (`prompts/sales_summary.md`):
- Подгружает `prompts/digital_clouds_context.md`
- Формат с `<b>` (Telegram-HTML), жирно выделены ключевые цифры
- Блок «План/факт» с emoji-индикатором 🔴/🟡/🟢
- Конверсия WON/лиды
- В конце — обращение «играющего РОПа» по имени менеджера. Формула 1+1+1+1: оценка → за что похвалить (обязательно) → конкретное действие на завтра → подбадривающая фраза. Жёстко при нулях, без оскорблений. Цель — чтобы менеджер не хандрил.

**.md-attachment**: после основного сообщения шлётся `calls_transcripts_<N>d.md` со всеми разобранными звонками (разделы per-менеджер и per-звонок: метаданные + 3-строчный AI-разбор + полный диаризованный транскрипт).

**Ручной запуск:**
```bash
docker compose exec bot python scripts/test_sales_report.py <bitrix_user_id> [days] [--no-send]
```

### Recruiter «Глафира» (Potok.io)

- Access control: `RECRUITER_ALLOWED` (TG IDs через запятую)
- FSM: `Recruiter.choosing_job` → `confirming` → `scoring | contacting | inviting`
- **Загрузка кандидатов**: `/api/v3/jobs/{id}/ajs_joins.json` (cursor pagination). Фильтр `active=False` — пропускаем рефузнутых/нанятых/архивных (они появляются в API но в UI Potok их не видно)
- **Скоринг** (`resume_scorer.score_applicant`):
  - Claude Sonnet, prompt с DC-вакансией + опц. секцией `Важно для CLAUDE:` (`extract_recruiter_instructions`)
  - Из job description вырезаются `Владельцы:` и `Ссылка для встречи:` строки (через `_strip_admin_lines`) — Claude их не видит
  - Возвращает JSON: score 0-100, breakdown, strengths, weaknesses, **questions** (5 вопросов для первого контакта)
  - Кэш вопросов в `_questions_cache: dict[applicant_id, list[str]]`
- **Push в Potok**: HTML-комментарий + префикс `{score:03d}-` к фамилии + JARVIS-маркер `<!-- JARVIS:QUESTIONS:[...] -->` для машинного парсинга позже
- **Auto-promote** (`push_scoring`): при `score > SALES_REPORT_HIGH_SCORE_THRESHOLD` (default 80) и текущей стадии в `POTOK_HIGH_SCORE_SOURCE_STAGES` (default `Добавлен,Откликнулся`) → `move_applicant_to_stage(target=POTOK_HIGH_SCORE_STAGE)` (default `Контакт с рекрутером`)
- **Stage filtering**: `_filter_by_stage` (job_id, stage_name) для стадий "Интервью с рекрутером" и "Интервью с менеджером" (`MANAGER_INTERVIEW_STAGE_NAME`). Исключает `state_id != None` и `active == False`

**Кнопка «📞 Связаться с кандидатами» (стадия «Интервью с рекрутером»):**
- Per-candidate карточка → «✉️ Написать» — отправляет intro («Я представляю компанию `RECRUITER_COMPANY` …») + вопросы (одним сообщением, нумерованные)
- «✉️ Написать всем» — bulk с throttle 1.5с между сообщениями, обрабатывает `FloodWaitError`/`PeerFloodError`, прогресс каждые 5
- Канал: Telegram через `userbot.send_to_user` (если есть `resolve_phone`) → fallback **HH через PotokFrontendClient** (`accounts[].url ?t=<channel_id>` → POST `/client_api/jobs/{job}/{applicant}/communication/communicate.json` с payload `{"communication_envelopes": [{"provider":"headhunter","channels":[...],"message":{"body":...}}]}`)
- HH работает только если у кандидата есть активный negotiation (HH deprecated cold messaging для employer)
- При успехе сохраняем `recruiter_contacts` (telegram_user_id → applicant_id, job_id) — для отслеживания ответов
- Финальный отчёт по статусам (sent / sent_hh / no_phone / no_questions / no_channel / send_failed / hh_failed) с поимёнными списками

**Кнопка «📅 Пригласить на собеседование» (стадия «Интервью с менеджером»):**
- Парсит `Владельцы: @vasya,@petya` и `Ссылка для встречи: https://...` из описания вакансии (`_extract_owners`, `_extract_meeting_link`)
- Резолвит имена владельцев через `bitrix.find_user_by_nickname`
- Шлёт через Telegram (userbot) приглашение с ссылкой на Bitrix calendar-sharing
- Pre-validation: если в описании нет ссылки → ошибка с предложением добавить

**Auto-reject входящих ответов кандидатов:**
- В `main.py _on_candidate_reply` (зарегистрирован на `userbot.set_reply_handler`) для каждого входящего:
  1. Lookup `recruiter_contacts` по `sender_id`
  2. `potok.post_candidate_reply` — сохраняем как комментарий в Potok (формат «❓ Заданные вопросы:» + «💬 Ответ кандидата:»)
  3. `classify_rejection_intent(text, ai_client)` через Haiku по `prompts/rejection_classifier.md` → {score, reasoning}
  4. Если `score > REJECTION_CLASSIFIER_THRESHOLD` (default 70) → `potok.set_applicant_active(active=False)` + audit-комментарий

### Мисис Хадсон (Weekly P&Q Analyst)

Еженедельный аудит worklog'ов отдела Production&Quality. Cron Пн 11:00 Нск.

**Что считает**:
- Набор проектов аудита = `dcj_projects` где `direction = "WEB - ПиК"` (≈288 из DCJ.xlsx) **плюс** `EXTRA_AUDIT_PROJECTS` (`hudson_analyzer.py`) — проекты по ключу из других направлений, которые тоже считаем. Сейчас там `COZYHOME` (внешний, в DCJ.xlsx под «Маркетинг»), `MZNN` (внешний, в dcj_projects его нет → `is_internal=0` из значения override) и `DCNEW` («Новый корпсайт DC», внутренний `1`, в dcj_projects под АУП). Override применяется на лету при загрузке → переживает пере-импорт DCJ.xlsx. Хочешь добавить проект — допиши `ключ: 0|1` (0 внешний / 1 внутренний) туда.
- Для каждого разработчика из `hudson_managers` (15 человек, 4 менеджера) — Jira worklog за прошлую полную неделю (Пн-Вс) через `services/jira_worklog.py`. **Тянем БЕЗ фильтра по проектам** (`project_keys=None`), потом бьём на in-scope / out-of-scope — иначе часы вне набора молча выпадали (причина жалоб «мой лог не учтён»).
- Суммирует часы → `total / internal / external` (internal по `is_internal=1`) **только по in-scope**. Часы вне набора → `out_of_scope_entries` / `out_of_scope_hours` (в total НЕ идут, только в .md-сверку).
- **Простой (bench)** — отдельная корзина `downtime_*`. Проекты-инстансы простоя (`DOWNTIME_PROJECT_KEYS_FALLBACK = DCBE/DCFE/DCAP/DCQQ/DCDE` + детект по имени `Простой %` через `_load_downtime_keys`). В DCJ.xlsx они `is_internal=1` → раньше ошибочно валились во внутренние часы (ложный 🔴 у людей на скамейке). Теперь: проверяются ПЕРВЫМИ при разбиении (формально они в `projects`), идут в `total_hours` (человек был доступен), но НЕ в internal/external и НЕ в bad-comment классификацию. Показываются отдельной строкой `💤 простой Xh` + отдельный .md + Jira-задача менеджеру.
- Норма по ТК РФ — `compute_weekly_norm()`:
  - База `WEEKLY_HOURS_NORM=32h` минус 8h за каждый рабочий (Пн-Пт) праздник из производственного календаря РФ (`services/holidays_api.py` — `isdayoff.ru`, кэш в памяти).
  - Минус 8h за каждый рабочий день отпуска/больничного/командировки (`bitrix.get_absences()` — `absence.list`, fallback на `calendar.event.get` по ключевым словам когда HR-модуль не установлен; флаг `_ABSENCE_LIST_DEAD` кэширует 404).
  - `weekly_norm <= 0` → `on_leave=True` (всю неделю пропустил, не оцениваем).
- Плохие комменты — **Haiku через OpenRouter** (`anthropic/claude-haiku-4.5`, не Claude CLI subscription — потому что 150+ вызовов на прогон, иначе квоту сожжёт), классификатор `prompts/hudson_bad_comment.md` (мягкий: ≤30мин комменты «дейлик/викли» норма, ловит только явные косяки — «работа», «правки», пустые). Sem(5) параллель, 60с timeout + 1 повтор.

**Рассылка** (`services/hudson_notifier.py`):
- Менеджерам (DM): краткий per-dev summary (`🔴 Гусев: 32.0h/<b>17.2h</b> (внутр выше 8h)` + счётчик плохих коммов).
- Алине Васьковой (РОП P&Q, `HUDSON_DEPT_HEAD_BITRIX_ID=37`): то же.
- В группу `HUDSON_CHAT_ID`: шапка с тэгами менеджеров (`tg://user?id=…`) + AI-мотивашка от Claude CLI (1 вызов/нед, опускается на subscription) + per-manager блоки.
- Всем — **4 .md аттачмента**: `bad_comments_<period>.md` (все плохие комменты с кликабельными Jira-ссылками `[PQ-918](jira.dclouds.ru/browse/PQ-918)`), `internal_hours_<period>.md` (внутренние часы по задачам, сгруппированы per-dev), `downtime_<period>.md` (простой по разработчикам — часы + issue + коммент, для приёмки менеджером) и `all_worklogs_<period>.md` (**ВСЕ** worklog'и каждого разработчика для сверки — каждая запись отдельной строкой: дата, issue-ссылка, часы, коммент; разбито на «✅ Учтено» / «💤 Простой» / «⚠️ Не учтено (проект вне аудита)» с ключом проекта — чтобы человек видел, почему его лог не зачёлся). Полные данные в .md, в Telegram только сводка чтобы не упереться в лимит 4096 байт (`TG_MAX=3800` + `_split_block` режет по строкам).

**Jira-задачи в проекте `PQ`** (если `HUDSON_SKIP_JIRA=false`):
- «Подтвердить внутренние часы» — на каждого менеджера, assignee = его Jira-username (из `hudson_managers.manager_jira_username`, резолвится через email при seed). Description содержит per-dev разбивку внутренних часов по issue и список плохих коммов.
- «Принять простой» — на каждого менеджера, у кого есть разработчики с `downtime_hours > 0`. Description: per-dev разбивка простоя по issue. Assignee = менеджер.
- «Отгул: <разработчик>» — если `is_under_norm`, assignee = менеджер.

**Кнопка «🏠 Мисис Хадсон» в меню**:
- Доступ через `HUDSON_ALLOWED` (TG IDs).
- При клике — полный отчёт **в личку нажавшему** (не в общий чат). Сообщения + 3 .md.
- Если у юзера DM с ботом закрыт — пишет в группу что надо открыть `/start` в личке.

**Маппинг менеджер↔разработчики**:
- В `services/hudson_repo.py:DEFAULT_MANAGER_MAPPING` (hardcoded, потому что в Bitrix у групп разработки head=NULL — связи нет).
- 4 менеджера / 15 разработчиков. Реорг 2026-06: Даниленко упразднён, его люди разведены (Геливанов/Присяжнюк → Бешеля; Овсянников/Осицын/Сердюков/Ушаков → новый менеджер Кузнецова Юлия, выделена из разработчиков). Ключ менеджера может быть «Имя Фамилия» (как «Кузнецова Юлия») — резолв менеджера разбивает на слова, как у разработчиков.
- Seed резолвит Bitrix ID/email/full_name/jira_username — `_find_user_by_last_name` (LAST_NAME filter + LIKE fallback, потому что у Осицына в Bitrix `LAST_NAME='Осицын '` с trailing-пробелом — exact match не находит).
- **Seed идемпотентен**: после upsert делает реконсиляцию — удаляет пары (менеджер, разработчик), которых больше нет в маппинге. Без этого перевод разработчика к другому менеджеру оставлял бы залипшую старую привязку (задвоение в аудите). Возвращает `(upserted, removed_pairs, warnings)`.
- Запуск: `scripts/init_hudson_db.py` — парсит `DCJ.xlsx` и сидит маппинг. **Менять маппинг → перезапустить этот скрипт на проде**, иначе таблица `hudson_managers` останется старой.

**Скрипты**:
- `scripts/run_hudson_now.py` — ручной прогон, поддерживает `--offset N` (позапрошлая неделя), `--since/--until`, `--dry-run`.
- `scripts/test_hudson.py` — console dry-run, печатает таблицу.

### Zabbix Monitor

- **Real-time** (`bot/routers/zabbix.py`): `@router.channel_post(F.chat.id == settings.zabbix_channel_id, F.text)` парсит сообщения от Zabbix-бота через regex (🔴 = открытие, 🟢 = закрытие; key = `Original problem ID`). UPSERT в `zabbix_problems` таблицу.
- **Cron 10:00** (`zabbix_check_unresolved_job`): берёт проблемы где `resolved_at IS NULL AND jira_task_key IS NULL AND opened_at <= now-24h`, фильтрует по severity (Warning+ через `ESCALATING_SEVERITIES = {Warning, Average, High, Disaster}`), создаёт Jira-задачу в `ZABBIX_JIRA_PROJECT` (default `DA`), маркирует `jira_task_key` чтобы не задвоить.
- **Backfill**: `scripts/scan_zabbix_month.py` использует **Telethon-userbot** (обычный бот не умеет читать историю каналов) для 30-дневного скана + создание задач по всему ещё открытому.

### B24 Lead Recon (xlsx → CRM лиды Косте)

Одноразовый перевод базы из `b24.xlsx` (вкладка «Клиенты», ~4500 строк) в B24 CRM как leads на Костю Карачева (`bitrix_id=697`, `SOURCE_ID="UC_XOBJMV"` = «База Яндекс»).

**Конвейер** (`scripts/b24_lead_from_xlsx.py`):
1. По каждому домену из xlsx — Claude CLI с `allowed_tools="WebSearch,WebFetch"`, промт `prompts/b24_lead_recon.md` — собирает JSON-карточку (company_name, отрасль, регион, контакты, новости, гипотезы болей, top-3 конкурента, industry_dynamics с ссылками на исследования).
2. Если WebFetch виснет/Cloudflare (180с timeout) → **fallback** на `prompts/b24_lead_recon_searchonly.md` (только WebSearch, 90с). Сайты которые WebFetch не открывает помечаются `site_unreachable=true`, лид всё равно создаётся с тем что нашлось через WebSearch.
3. Лид создаётся через `crm.lead.add`. **Полный recon** уходит в **timeline.comment** (правая колонка карточки) через `bitrix.add_timeline_comment()`. В `COMMENTS` лида — однострочный summary (виден в канбане).
4. Контакты: телефоны/emails в стандартные поля `PHONE`/`EMAIL`. Telegram/WhatsApp из соцсетей — в `IM`.

**Бэкфилл UF-полей** (`scripts/b24_lead_backfill_fields.py`) — отдельный проход, БЕЗ Claude, только Bitrix API:
- `UF_CRM_1779947430020` (string) — **Агентство Текущее** ← xlsx колонка C
- `ADDRESS_CITY` (standard) — **Город** ← regex «Регион: …» из COMMENTS
- `UF_CRM_1779951351014` (enum, 18 значений) — **Сфера** ← `classify_industry()` по keywords (Стоматология, Застройщики, Риелторы, Промышленное оборудование, B2B-сервис и инжиниринг, Медицина, и т.д. — см. `INDUSTRY_GROUPS`)
- `UF_CRM_1779947540127` (boolean Y/N) — **Есть сотовый** ← regex `^[78]?9\d{9}$` по PHONE
- `UF_CRM_1779947613495` (enum 4) — **Бюджет на рекламу** ← bucket xlsx revenue (`<500к | 500к-1М | 1М-3М | >3М`, exact labels `«до 500.000»`/`«500.000-1.000.000»`/`«1.000.000-3.000.000»`/`«Выше 3.000.000»`)

`FIELD_OVERRIDES` хардкодит UF-коды (title в Bitrix долго не подтягивается после создания поля). После переименования полей в UI auto-match по title тоже сработает.

**Чекпоинт-стейт** (`b24_processed.json`):
- Лежит в **`data/b24_processed.json`** (volume-mounted) — раньше был в корне репо, но docker compose up --build обнулял (`/app/` НЕ примонтирован). Скрипт делает миграцию из старого места если есть.
- Записывает каждый домен с `status: ok/skip_recon/error` + `lead_id`, `timeline_id`, `site_unreachable`, `ts`.
- При перезапуске пропускает все 3 статуса (раньше пропускал только `ok` и каждый раз тратил 5 мин на висящие сайты впустую).

**Восстановление** (`scripts/rebuild_b24_state.py`): тянет `crm.lead.list` по `%SOURCE_DESCRIPTION="Recon из b24.xlsx"`, парсит домен из `WEB[0]` или TITLE, восстанавливает state. Использовался когда обнаружили что state писался в /app/ (ephemeral) — было 1000+ лидов в B24 при 108 в state.

**Доп. скрипты**:
- `scripts/b24_lead_set_source.py` — массово выставляет `SOURCE_ID="UC_XOBJMV"` (была временно `OTHER`).
- `scripts/dump_unreachable.py` — выгружает список доменов с `site_unreachable=true` + кликабельные ссылки на лиды для ручного прохода.

**Запуск на сервере**:
```bash
screen -S b24 -dm bash -c 'docker compose exec -T bot python scripts/b24_lead_from_xlsx.py --all > data/b24_run.log 2>&1'
```
~2.5 мин/лид. 4500 доменов = ~7 суток непрерывной работы (включая subscription weekly quota Claude Max — реально сжирает её за пару дней).

### Cicero, Contract, Meeting, Free Slots, Lead, Image, Socrates — без изменений, см. `prompts/` и роутеры.

### Socrates (Meeting Analyser)

- Entry: 🎓 Сократ → FSM `Socrates.waiting_for_url`
- Per-user `asyncio.Lock` против параллельных пайплайнов
- SSRF guard: DNS resolution + private-address blocklist на каждый redirect hop
- URL only (Telegram cap 20 MB). Поддерживаются Yandex.Disk, Google Drive (`?id=`/`/file/d/{ID}/view` → rewrite на `drive.usercontent.google.com/download?...&confirm=t`), прямые URL
- Content-Type check на ответе после редиректов — если text/html, abort
- ffmpeg → mono 16kHz opus 24kbps; `MEETING_MAX_MINUTES` (default 90)
- Gemini (диаризация) → Claude×2 (review + brief). 3 артефакта `.md`
- OpenRouter: **retry один раз** при `provider_overloaded` (HTTP 200 + embedded 503 in choice payload) и любом 5xx через `_transcribe_once` → возвращает `TranscriptionResult(retryable=True)` → wrapper повторяет через 5с

### Daily Summary / Wed Frog / Mon Poster

См. соответствующие jobs в `scheduler/jobs.py`. Wed Frog — рандомный стиль из `FROG_STYLES`. Mon Poster — конструктивистский плакат с IT-героем.

## Database Schema (aiosqlite)

```sql
users (telegram_id PK, bitrix_user_id, bitrix_domain, display_name, is_active, created_at)
group_chats (chat_id PK, chat_title, added_at, summary_enabled)
message_buffer (id PK AUTO, chat_id, sender_id, sender_name, text, sent_at) + INDEX(chat_id, sent_at)
muted_groups (chat_id PK)

-- Recruiter ↔ Telegram бридж: кому из кандидатов писали, чтобы ловить их ответы
recruiter_contacts (
    telegram_user_id PK, phone, applicant_id, job_id, job_name, applicant_name, created_at
)

-- Zabbix problem state (real-time + backfill)
zabbix_problems (
    problem_id PK, host, name, severity, opened_at, resolved_at, jira_task_key,
    raw_text, last_seen
) + INDEX idx_zabbix_unresolved(resolved_at, jira_task_key, opened_at)

-- Мисис Хадсон — справочник проектов из DCJ.xlsx (642 проекта, 289 в WEB-ПиК)
dcj_projects (
    project_key PK, name, is_internal, direction, category, updated_at
)

-- Мисис Хадсон — маппинг менеджер↔разработчики (DC-specific, в Bitrix у групп head=NULL)
hudson_managers (
    manager_name + developer_pattern PK,
    manager_bitrix_id, manager_jira_username, manager_full_name,
    developer_bitrix_id, developer_email, jira_username
)
```

## Config (.env)

### Required
- `BOT_TOKEN` — Telegram BotFather

### AI
- `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_REFRESH_TOKEN`
- `CLAUDE_CLI_PATH` (default `claude`)
- `CLAUDE_MODEL` — опц. override (например `claude-opus-4-7`)
- `CLAUDE_OAUTH_CLIENT_ID` — default official Claude Code ID
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (default `google/gemini-2.5-pro`), `OPENROUTER_TIMEOUT` (default 300s)

### Bitrix24
- `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `BITRIX_DOMAIN`, `BITRIX_REFRESH_TOKEN` (first run only)
- `BITRIX_TELEGRAM_FIELD` (default `UF_USR_1678964886664`)
- `BITRIX_EMAIL_GUESTS_SCAN_MAX` (2000), `BITRIX_EMAIL_GUESTS_MULTIPLIER` (3)

### Potok.io
- `POTOK_API_TOKEN`, `POTOK_BASE_URL` (default `https://app.potok.io`)
- `POTOK_AFTER_CONTACT_STAGE` (default `Скриннинг резюме` — типо «Скрининг», на стороне Potok с опечаткой)
- `POTOK_HIGH_SCORE_THRESHOLD` (80), `POTOK_HIGH_SCORE_STAGE` (`Контакт с рекрутером`), `POTOK_HIGH_SCORE_SOURCE_STAGES` (`Добавлен,Откликнулся`)
- **Frontend session** (для HH-messaging через `/client_api/*`):
  - `POTOK_FRONTEND_ACCESS_TOKEN`
  - `POTOK_FRONTEND_CLIENT`
  - `POTOK_FRONTEND_UID`
  - Извлекаются из браузерной сессии (DevTools → Network → headers любого запроса на app.potok.io). Токены статичны, TTL ~5 месяцев.

### Userbot (Telethon — для рекрутёра и Zabbix backfill)
- `TELETHON_API_ID`, `TELETHON_API_HASH` (one-time с my.telegram.org)
- `TELETHON_SESSION` — StringSession, сгенерировать через `scripts/create_userbot_session.py`
- `RECRUITER_COMPANY` (default `Digital Clouds`), `RECRUITER_NAME` — для intro в первом сообщении кандидату

### Sales analytics
- `SALES_REPORT_BITRIX_USER_IDS` — Bitrix IDs продажников через запятую
- `SALES_REPORT_RECIPIENTS` — TG IDs кому слать (можно группы с минусом)
- `SALES_REPORT_MONTHLY_PLAN` (default `220000` ₽)
- `SALES_REPORT_DEAL_CATEGORIES` (default `27,31,33`) — какие воронки учитывать
- `SALES_REPORT_ACTIVE_DEAL_PATTERNS` (default `кп,договор,счёт,счет,переговор,согласи,кэв провед,отработк,оплат`) — паттерны имён стадий для «горящих»
- `SALES_REPORT_HOT_STAGES` — устаревший, использует substring match по STAGE_ID

### Zabbix
- `ZABBIX_CHANNEL_ID` (numeric, начинается с `-100`)
- `ZABBIX_JIRA_PROJECT` (default `DA`)
- `ZABBIX_THRESHOLD_HOURS` (default 24)

### Мисис Хадсон
- `HUDSON_DEPT_HEAD_BITRIX_ID` (default `37` — Алина Васькова, РОП P&Q; получает копию отчёта)
- `HUDSON_ALLOWED` — TG IDs кому доступна кнопка «🏠 Мисис Хадсон» (я + менеджеры P&Q)
- `HUDSON_CHAT_ID` — общая группа отчёта (например `-1002588304733`). Бот должен быть в группе.
- `HUDSON_SKIP_JIRA` (default `false`) — если `true`, Telegram-рассылка работает, но Jira-задачи в PQ не создаются (на время тестов чтобы не плодить задачи).

### DaData (Штирлиц)
- `DADATA_API_KEY`, `DADATA_SECRET_KEY` (secret для clean/standard API, не обязателен для find/suggest)

### Auto-reject (rejection classifier)
- `REJECTION_CLASSIFIER_THRESHOLD` (default 70)

### OpenClaw (Марфа)
- `OPENCLAW_URL`, `OPENCLAW_TOKEN`, `OPENCLAW_AGENT_ID` (`main`)

### Jira
- `JIRA_URL`, `JIRA_USERNAME`, `JIRA_PASSWORD`

### Access lists
- `GLAFIRA_ALLOWED` (Марфа/OpenClaw — TG IDs)
- `RECRUITER_ALLOWED` (Глафира/Potok — TG IDs)

### Scheduled content
- `WEDNESDAY_FROG_CHAT_ID` (default 0 = disabled)
- `MONDAY_POSTER_CHAT_ID` (0)

### Socrates
- `FFMPEG_BIN` (`ffmpeg`), `MEETING_MAX_MINUTES` (90)

### Webhook
- `WEBHOOK_TOKEN`

### Other
- `DB_PATH` (`data/arkadyjarvis.db`), `SUMMARY_HOUR` (19), `SUMMARY_MINUTE` (0), `TIMEZONE` (`Asia/Novosibirsk`)

## Coding Guidelines

- **No hardcoded field IDs** — Bitrix UF_* в `config.py`
- **No hardcoded user IDs** — access lists в `.env`
- **No hardcoded secrets** — `.env` через pydantic-settings
- **JSON from AI** — `utils.parse_json_response()`
- **OpenClaw isolation** — всегда `user_id` в `openclaw.stream_chat()`
- **Lead creation** — `SOURCE_ID`/`SOURCE_DESCRIPTION` + creator's Telegram contact в COMMENTS
- **HTML-escape user strings** — всегда `html.escape()` для user-controlled (имена, компании, AI-output). Telegram default parse_mode = HTML.
- **Long AI answers** (>4000 chars) — отдавать как `.md` attachment, не пытаться чанковать HTML.
- **AIClient injection** — сервисы получают `ai_client` как параметр, не создают свой
- **DB user в callbacks** — использовать middleware-инжекцию `db_user`, не fetcher.
- **Prompts in `prompts/`** — добавил `.md` → `load_prompt(name)`. Placeholders типа `{data_json}` / `{dc_context}` подставляет caller через `.replace()`.
- **CLI tools security** — `allowed_tools` параметр пробрасывать ТОЛЬКО для read-only (WebSearch/WebFetch). НИКОГДА для Bash/Read/Write/Edit — это RCE.
- **CLI cwd=/tmp** — иначе Claude CLI подхватит project CLAUDE.md как system context.
- **Haiku для дешёвых классификаторов** — `model="claude-haiku-4-5-20251001"` (полный ID — alias `haiku` может не разрешиться в старых CLI).
- **Bitrix stage filtering** — для лидов используй `STATUS_SEMANTIC_ID` (`S`/`F`/null). Для сделок — `CATEGORY_ID` (whitelist воронок) + опц. NAME pattern на стадиях. `CLOSED=N` недостаточно — есть кастомные «парковочные» стадии с семантикой `P` но фактически не активные.
- **N+1 в API** — для `crm.stagehistory.list` и подобных нет фильтра по user, нужен N+1 fetch по `OWNER_ID=deal_id`. Параллелить через `asyncio.Semaphore(5)`.

## Running

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Health: `curl localhost:8001/api/health`

Docker: `docker compose up --build` (port 8002), logs: `docker compose logs -f bot`

## Known Issues

- Claude CLI: `CLAUDE_CODE_OAUTH_TOKEN` refresh tokens — single-use, потеря = re-auth
- Email guests cannot be created via Bitrix REST API (UI only)
- Bitrix OAuth — shared file-based, не per-user
- Email guest cache — in-memory, throttle 0.3s между батчами, leave-unloaded on full failure
- OpenRouter image gen — может silently refuse по content policy (0 completion tokens = refusal)
- Potok scored: convention `^\d{3}-` префикс к фамилии — fragile, при ручном переименовании ломается
- Potok API SSL: русские CA — работает с Ubuntu, может падать с Mac без российского CA bundle
- Tailscale + OpenVPN: redirect-gateway конфликт — без server-side split tunnel не запустить вместе
- Некоторые callback handlers (`meeting.py`, `free_slots.py`) ре-фетчат `db_user` — не баг, но дубликат запроса
- Socrates SSRF guard — узкое DNS-rebinding окно (TOCTOU). Полностью закрывается pinned-IP transport, не делали. Компенсация: внутренний Tailscale-only deployment + Bitrix-bound `/start` auth.
- **Potok admin metadata в job description**: правила `Владельцы:` / `Ссылка для встречи:` парсятся регекспами в `recruiter.py`. Меняешь формат — лезь править regex.
- **Potok `Контакт с рекрутером` название** — в Бытриксе24 у клиента может назваться по-другому. Аналог: `CANNOT_CONTACT` lead status у нас имеет displayed name «В работе» (внутренний код всегда `CANNOT_CONTACT`, переименовать нельзя). Везде где сравниваем по имени — case-insensitive.
- **Potok `/client_api/*` (HH messaging)** — frontend tokens живут ~5 месяцев и не ротируются. При 401 — переэкстрагировать из браузера. Документации нет, ловили через DevTools.
- **DaData финансы — NULL** на бесплатном тарифе. Берём финансы из ГИР БО ФНС.
- **VOK API Saby** — платная подписка. Через интерактивный логин (без app credentials) не работает, нашими cookies спрятанный VOK тоже не пускает. Не интегрируем.
- **`crm.stagehistory.list` нельзя фильтровать по user** — только по `OWNER_ID=deal_id`. N+1 по сделкам менеджера.
- **`tasks_done` метрика убрана** — в DC не работают по «задачам», метрика бесполезна.
- **State-файлы должны жить в `data/`** — это volume-mounted директория (`./data:/app/data`). Любые `Path("xxx.json")` в корне `/app/` УБИВАЮТСЯ при `docker compose up --build`. Прецедент: `b24_processed.json` лежал в корне → 1000 лидов в B24 при 108 в state. `scripts/rebuild_b24_state.py` восстанавливает из API.
- **Bitrix CRM `COMMENTS` поле — MySQL utf8 (3-байтовая)** — 4-байтные emoji (📊 💡 🔧 и пр., supplementary plane U+10000+) обрезают всё поле начиная с первой эмодзи. В `b24_lead_from_xlsx.py:_strip_emoji()` чистим. Стандартные русские буквы и `₽` (BMP, ≤3 байта) — норм.
- **Bitrix CRM `COMMENTS` — BBCODE, не HTML** — `<b>...</b>` ломает field. Используем `[B][/B]`, `[BR]`. В `timeline.comment` — обычные `\n` норм.
- **Claude CLI читает токен — env var `CLAUDE_CODE_OAUTH_TOKEN` ПЕРВЕЕ чем `~/.claude/credentials.json`**. Если Python wrapper обновил токен через `claude_token.ensure_fresh_token()`, прямой `docker exec bot claude --print …` всё равно ловит 401 (env от .env стейл). Фикс: `_sync_cli_credentials()` после рефреша пишет `~/.claude/credentials.json` в формате `{claudeAiOauth: {accessToken, refreshToken, expiresAt, scopes}}`. Прямой `claude` тоже работает.
- **WebFetch от Claude CLI ходит с Anthropic dataclass IP** — многие RU-сайты (Cloudflare, гео-блок, антибот) висят бесконечно, stderr пустой. Решение в `b24_lead_from_xlsx.py`: 180с timeout → fallback на WebSearch-only `prompts/b24_lead_recon_searchonly.md`. Сайт помечается `site_unreachable=true`, лид всё равно создаётся.
- **Bitrix `absence.list` 404** — требует HR-модуль (платный). Fallback в `bitrix_client._users.get_absences()` через `calendar.event.get` + поиск ключевых слов («отпуск/болеет/командировк»). Кэшируем `_ABSENCE_LIST_DEAD=True` после первого 404 чтобы не дёргать.
- **APScheduler НЕ догоняет пропущенные cron'ы после рестарта контейнера** — например ребут после 11:00 Пн вайпает hudson_weekly до следующего понедельника. Запускать руками `scripts/run_hudson_now.py` или добавить `misfire_grace_time` (пока не сделали).
- **Industries для B24** — Claude каждый раз даёт уникальную AI-формулировку отрасли (1409 уникальных из 1479 лидов). В скрипте бэкфилла keyword-классификация в 18 категорий (`INDUSTRY_GROUPS`), порядок важен — узкие категории (Стоматология, Риелторы, Промышленное оборудование) идут РАНЬШЕ более широких (Медицина, Застройщики, B2B-сервис) — первое совпадение выигрывает.
