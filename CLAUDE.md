# ArkadyJarvis

Multi-user Telegram bot (BotFather, NOT userbot) для команды Digital Clouds: суммаризация чатов, Bitrix24 calendar/CRM, Jira, AI-ассистенты (общий, юрист, рекрутёр, офис-менеджер, бизнес-разведка), генерация изображений, скоринг резюме (Potok.io), проверка договоров, voice-to-lead, аналитика отдела продаж, аудит worklog'ов P&Q, мониторинг Zabbix, регулярный мотивационный контент.

## Tech Stack

- **Python 3.11+**, aiogram v3 (Telegram Bot API), FastAPI + Uvicorn
- **AI**:
  - Claude CLI (subscription, `--print` subprocess) — основной путь для текстовых задач. Модель по умолчанию — CLI-дефолт (Sonnet), т.к. `CLAUDE_MODEL` пуст. Haiku для дешёвых классификаторов передаётся **явно** per-call (`model="claude-haiku-4-5-20251001"`).
  - OpenRouter — аудио/видео (Gemini 3 Pro Image для генерации, Gemini 2.5 Pro для транскрипции voice / mp3 со звонков) **и** текстовый `complete_text()` для массовых классификаторов мимо subscription-квоты (Хадсон, `anthropic/claude-haiku-4.5`, 150+ вызовов на прогон).
- **Userbot**: Telethon (StringSession) для чтения истории каналов (Zabbix backfill), отправки сообщений кандидатам с личного аккаунта рекрутёра
- **Integrations**: Bitrix24 REST + Bitrix calendar-sharing короткие ссылки; Jira REST; Potok.io ATS (REST API + frontend `/client_api/*` через 3 статических заголовка DeviseTokenAuth — `access-token`/`client`/`uid` — для HH-messaging); OpenClaw (browser RPA); DaData (карточки ЮЛ по ИНН, бесплатный API key); ГИР БО ФНС (`bo.nalog.gov.ru` — бухотчётность, без авторизации); SBIS/Saby — рассматривали для разведки, отказались (нужна платная VOK-лицензия)
- Uvicorn owns the event loop; aiogram polling запускается как `asyncio.create_task()` в FastAPI lifespan
- APScheduler — все cron-задачи. **Все `CronTrigger` объявлены в `main.py:156-250`**, в `jobs.py` только тела функций.
- aiosqlite — все persistent state
- pydantic-settings — `.env`
- pypdf + python-docx — извлечение текста для contract check / Cicero

## Project Structure

```
app/
  main.py                  # FastAPI app, lifespan, polling, ВСЕ CronTrigger'ы, инжекция сервисов
  config.py                # pydantic-settings Settings — все ENV. settings = Settings() на импорте
  db.py                    # aiosqlite SCHEMA + MIGRATIONS + CRUD (users, group_chats, message_buffer,
                           #   muted_groups, recruiter_contacts, zabbix_problems, dcj_projects,
                           #   hudson_managers, schema_version)
  utils.py                 # parse_meeting_time/attendees, md_to_telegram_html, parse_json_response,
                           #   merge_intervals, split_telegram_html, strip_numbered_item
  summarizer.py            # Claude-суммаризация чатов и daily overview
  version.py               # __version__ (единственный источник версии — тегов в git нет)
  bot/
    create.py              # create_bot() + create_dispatcher() — порядок роутеров КРИТИЧЕН
    middlewares.py         # ErrorMiddleware + AuthMiddleware (Message + CallbackQuery)
    routers/
      start.py             # /start (Bitrix-auth по @username), /help, MENU_KB, hint:* dispatcher
                           #   (включая Stirlitz/Recruiter/Glafira/Hudson), команда, мои встречи, work:*
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
      stirlitz.py          # FSM Stirlitz — разведка по компании/человеку (DaData + ГИР БО + WebSearch*)
      glafira.py           # «Марфа» (AI офис-менеджер) — OpenClaw streaming. persona в UI = «Марфа»,
                           #   файл/класс остался Glafira
      recruiter.py         # «Глафира» (AI рекрутёр) — Potok.io скоринг + Telethon-рассылка + HH-fallback.
                           #   Persona UI = «Глафира», класс = Recruiter
      hudson.py            # «Мисис Хадсон» — БЕЗ единого @router-хендлера, роутер пустой.
                           #   Вход только через hint:hudson → start.py:375 → enter_hudson()
      zabbix.py            # channel_post handler для Zabbix-канала → SQLite
      work.py              # work:* callback — НЕ dead code, живой хендлер в start.py:163-168
      group.py             # on_bot_added/removed + BotMentionFilter (@упоминание → MENU_KB)
      buffer.py            # Catch-all (LAST!) — буферизация group messages (кроме съеденных group.py/FSM)
  services/
    ai_client.py           # AIClient — Claude CLI wrapper. Параметры: prompt, timeout, system_prompt,
                           #   allowed_tools, model
    claude_token.py        # Два режима: long-lived (ранний return) и legacy auto-refresh. См. «Claude token»
    bitrix_client/         # BitrixClient — пакет с миксинами
      __init__.py           # композиция миксинов
      _base.py              # _BitrixBase — OAuth file-based, httpx(timeout=30), auto-refresh, _batch_request
      _calendar.py          # _BitrixCalendarMixin — события, free slots, create_meeting, get_user_events
      _crm.py               # _BitrixCRMMixin — ТОЛЬКО create_lead + add_timeline_comment
      _timeman.py           # _BitrixTimemanMixin — timeman API (жив, дёргается из work.py)
      _users.py             # _BitrixUsersMixin — user.get, email guests, find_user_by_nickname,
                            #   get_my_team, get_absences
    jira_client.py         # JiraClient — async context manager. Auto-retries без assignee
    jira_worklog.py        # fetch_worklogs — Jira worklog для Хадсона (N+1 по issues, без фильтра проектов)
    holidays_api.py        # isdayoff.ru — производственный календарь РФ, кэш в памяти
    hudson_analyzer.py     # build_reports — ядро Хадсона. EXTRA_AUDIT_PROJECTS, PART_TIME_NORMS
    hudson_notifier.py     # notify() — рассылка Хадсона (DM/группа/Jira/.md)
    hudson_repo.py         # DEFAULT_MANAGER_MAPPING + seed_default_managers
    document_parser.py     # Извлечение текста из PDF/DOCX/TXT
    ffmpeg_tool.py         # convert_to_opus, probe_duration, split_audio — Socrates stage 0
    meeting_downloader.py  # Скачивание из Yandex.Disk / Google Drive / прямой URL. MAX_DOWNLOAD_BYTES=1 GiB
    meeting_pipeline.py    # Socrates orchestration
    openclaw_client.py     # OpenClawClient — HTTP SSE, per-user agent isolation
    openrouter_client.py   # generate_image + transcribe_voice(format=...) + complete_text (текстовый путь)
    prompts.py             # load_prompt(name) — чтение prompts/<name>.md
    potok_client.py        # PotokClient — Potok.io REST (Bearer): jobs, applicants, scoring push,
                           #   stage move, кэш questions, post comments
    potok_frontend.py      # PotokFrontendClient — frontend /client_api/* через DeviseTokenAuth — HH
    potok_models.py        # Pydantic: Job, Applicant (+accounts), Resume, CvParams, AjsJoin (+state_id),
                           #   AjsJoinJob (+active — флаг ВАКАНСИИ), ScoringResult, ScoreBreakdown
    resume_scorer.py       # score_applicant(job, applicant, *, ai_client) — prompts/recruiter_scoring.md
    rejection_classifier.py # classify_rejection_intent(text, ai_client) — 0-100 (см. Known Issues: Sonnet)
    userbot.py             # UserbotClient (Telethon) — send_to_user, resolve_phone, set_reply_handler
    dadata_client.py       # DaDataClient — find_by_id (по ИНН), suggest (по названию)
    giro_client.py         # GiroClient — bo.nalog.gov.ru: search org, fetch bfo (выручка/активы по годам)
    stirlitz.py            # Orchestrator: classify_intent (Haiku) → company_inn|company_name|person|clarify
    sales_analytics.py     # DailySalesActivity + collect_user_activity (Bitrix + voximplant + транскрипция)
    zabbix_monitor.py      # parse_zabbix_message (regex по 🔴/🟢), check_unresolved_and_create_jira
  scheduler/
    jobs.py                # Тела джобов (триггеры — в main.py): daily_summary_job, wednesday_frog_job,
                           #   monday_poster_job, sales_dept_summary_job, zabbix_check_unresolved_job,
                           #   hudson_weekly_job
  api/
    routes.py              # GET /api/health, POST /api/bitrix/notify, POST /api/bitrix/broadcast
prompts/                   # 22 файла
  contract_check.md        # Чек-лист проверки договора
  cicero.md                # Юрист-консультант (ГК, КоАП, АПК, НК)
  jira_task_template.md    # Reformat задачи под наш шаблон
  voice_transcribe.md      # Diarization (Lead voice + Socrates stage 1)
  wednesday_frog.md        # Мем-лягушка с {style}
  monday_poster.md         # Constructivist IT-плакат
  meeting_review.md        # Socrates stage 2
  meeting_brief.md         # Socrates stage 3
  ask_ai_system.md         # Персонаж «Джарвис Аркадия» для AskAI
  digital_clouds_context.md # SHARED {dc_context} — ТОЛЬКО sales_summary.md и sales_call_analysis.md
  stirlitz.md              # Card компании по DaData+ГИР БО. Плейсхолдер только {data_json}
  stirlitz_person.md       # Recon человека (LinkedIn, Habr, VK)
  stirlitz_intent.md       # Haiku-диспетчер: company_inn|company_name|person|clarify (JSON)
  rejection_classifier.md  # Классификатор отказа в ответе кандидата (0-100)
  recruiter_scoring.md     # Скоринг резюме. Подстановка регексом \{(\w+)\} — НЕ .format()
  hudson_bad_comment.md    # Классификатор плохих worklog-комментов (мягкий)
  sales_summary.md         # Общий отчёт по продажнику ({dc_context}, «играющий РОП»-укол)
  sales_call_analysis.md   # Per-call разбор «📝 Суть / ✅ Хорошо / ⚠️ Улучшить» ({dc_context})
  b24_lead_recon.md        # B24 recon домена (WebSearch+WebFetch)
  b24_lead_recon_searchonly.md # Фолбэк без WebFetch
  retail_lead_recon.md     # Retail recon компании с выставки
  retail_lead_recon_searchonly.md # Фолбэк без WebFetch
data/
  arkadyjarvis.db          # SQLite database
  bitrix_tokens.json       # Bitrix OAuth (auto-refreshed)
  .claude_token.json       # Claude OAuth — ТОЛЬКО в legacy-режиме (см. «Claude token»)
  b24_processed.json       # Чекпоинт B24 recon
  retail_processed.json    # Чекпоинт retail recon
scripts/
  show_users.py            # CLI users + activity (7d)
  show_groups.py           # CLI group_chats counts
  find_bitrix_user.py      # Поиск Bitrix user_id по фамилии/имени
  release.sh               # Релизный хелпер
  test_claude_cli.py       # Проверка Claude CLI напрямую
  refresh_claude_token.py  # Ручной рефреш Claude-токена
  test_wednesday_frog.py   # Manual Wed frog
  test_monday_poster.py    # Manual Mon poster
  create_userbot_session.py # Сгенерировать TELETHON_SESSION (StringSession) — разовый интерактив
  scan_zabbix_month.py     # Backfill Zabbix history за 30 дней через userbot → Jira-задачи
  init_hudson_db.py        # Парсит DCJ.xlsx + сидит hudson_managers. МЕНЯЕШЬ МАППИНГ → ГОНЯЙ НА ПРОДЕ
  run_hudson_now.py        # Ручной прогон Хадсона: --offset N / --since --until / --dry-run
  test_hudson.py           # Console dry-run Хадсона, печатает таблицу
  test_sales_report.py     # Sales report end-to-end: <bitrix_user_id> [days] [--no-send] [--date YYYY-MM-DD]
  test_rejection_classifier.py # Прогон rejection LLM на встроенных кейсах
  list_user_leads.py       # Аудит активных лидов менеджера по статусам
  inspect_applicant.py     # Поиск кандидата по имени во ВСЕХ вакансиях (для «ghost» candidates)
  inspect_hh_channels.py   # Расшифровка accounts[].url ?t=<channel> у HH-кандидатов
  b24_lead_from_xlsx.py    # B24 recon-конвейер (b24.xlsx → лиды Косте)
  b24_lead_backfill_fields.py # Бэкфилл UF-полей B24-лидов (без Claude)
  b24_lead_set_source.py   # Массово выставить SOURCE_ID «База Яндекс»
  rebuild_b24_state.py     # Восстановить b24_processed.json из crm.lead.list
  dump_unreachable.py      # Домены с site_unreachable=true + ссылки на лиды
  retail_lead_from_xlsx.py # Retail recon-конвейер (retail.xlsx → лиды Добрякову)
  test_potok_events.py     # Дамп Potok events на applicant (для отладки questions marker)
  test_potok_move_stage.py # Brute-force подбора endpoint смены стадии в Potok (исторический)
  test_potok_communicate_frontend.py # Тест отправки HH через /client_api/communicate
  test_potok_hh_messaging.py # Discovery — какие endpoints доступны для HH
  test_potok_communicate.py # Те же endpoints через публичный Bearer (проверка что не пускают)
  test_sbis_auth.py        # SBIS/Saby — discovery interactive login (исторический, отказались)
  test_sbis_partner.py     # SBIS partner spp-rest-api проверка (исторический)
```

## Key Patterns

### Architecture

- **AIClient** (`services/ai_client.py`) — обёртка над `claude --print --output-format text`. Параметры:
  - `prompt` — текст
  - `timeout` (default 120)
  - `system_prompt` — добавляется через `--append-system-prompt` (только AskAI его использует)
  - `allowed_tools` — comma-separated **whitelist**, уходит в `--tools` verbatim
  - `model` — override `settings.claude_model` per-call. `claude_model` по умолчанию `""` → флаг `--model` не добавляется → CLI-дефолт. Классификаторы передают `"claude-haiku-4-5-20251001"` явно.
  - **Security**: `--tools <allowed_tools or "">` — **whitelist, НЕ deny-list**. Пустая строка = пустой whitelist = все встроенные тулзы выключены (prompt-only). Это защита от RCE: был critical bug, когда пользователь писал «сделай cd, ls» и CLI реально запускал шелл. Убрать флаг = вернуть дыру.
  - **Почему whitelist, а не `--disallowed-tools`** (v4.33.0, 43dadae): deny-list ломался целиком, когда CLI переименовывал/удалял тулзу — `MultiEdit` → exit 1 «matches no known tool» → падали ВСЕ AI-вызовы. И он молча пропускал новые тулзы. Не возвращать.
  - **Security**: subprocess запускается с `cwd="/tmp"` — иначе CLI подхватывает project CLAUDE.md как system context и считает все вопросы «вне темы».
  - `env.pop("CLAUDECODE")` — чтобы дочерний CLI не считал себя внутри сессии Claude Code.
  - ⚠️ **`--tools` даёт доступность, но НЕ разрешение** — WebSearch/WebFetch в проде не выполняются. См. Known Issues.
- **Claude token** (`services/claude_token.py`) — **два режима, переключатель = наличие `CLAUDE_REFRESH_TOKEN`**:
  - **Long-lived** (рекомендуемый, v4.34.0/c27123a): `CLAUDE_CODE_OAUTH_TOKEN` есть, `CLAUDE_REFRESH_TOKEN` **пуст/отсутствует** → `ensure_fresh_token()` делает ранний return, файл `data/.claude_token.json` не читается и не пишется. Токен из `claude setup-token` (~1 год). WARNING «token will not auto-refresh» на старте — ожидаемое поведение, не инцидент.
  - **Legacy auto-refresh**: `CLAUDE_REFRESH_TOKEN` задан → чтение/запись `data/.claude_token.json`, `asyncio.Lock`, single-use refresh, `_sync_cli_credentials()` пишет `~/.claude/credentials.json`.
  - Чтобы включить long-lived — **убрать** `CLAUDE_REFRESH_TOKEN` из .env (не добавить флаг). Читается `os.environ` напрямую → нужен `up -d --force-recreate`.
- **BitrixClient** — singleton, миксины (`_base`, `_users`, `_calendar`, `_crm`, `_timeman`). File-based OAuth (`data/bitrix_tokens.json`), auto-refresh. `_get_tokens` читает и парсит файл **на каждый запрос** (плюс: токен от другого процесса подхватывается сразу). `_BitrixBase.__init__` не зовёт `super().__init__()` → `__init__` в миксине не выполнится.
- **OpenRouterClient** — singleton. `generate_image(prompt, image_b64?)`, `transcribe_voice(path, audio_format="ogg")` (mp3 для записей звонков), `complete_text(prompt, model=...)` — текстовый путь мимо subscription-квоты.
- **PotokClient** — singleton, Bearer-токен через `POTOK_API_TOKEN`. In-memory cache вопросов (`_questions_cache`) — заполняется при `push_scoring`, читается при отправке вопросов. После рестарта — парсинг событий по маркеру.
- **PotokFrontendClient** — отдельный singleton для `/client_api/*` (HH-messaging). Auth через 3 заголовка DeviseTokenAuth (`access-token`, `client`, `uid`). Получаются один раз из браузерной сессии (DevTools → Network → любой XHR на app.potok.io). При пустых токенах **не падает** — тихо ставит `_client = None`; проверять через `is_configured`.
- **UserbotClient** (Telethon) — `send_to_user(user_id, text)`, `resolve_phone(phone)` через `ImportContactsRequest`. Слушает `events.NewMessage(incoming=True)`. В `main.py` регистрируется callback `_on_candidate_reply` — при входящем от tg_id из `recruiter_contacts` сообщение сохраняется в Potok + классифицируется на «отказ». Если score > порог — `potok.set_applicant_active(active=False)` + audit-комментарий.
- **JiraClient** — async context manager. Auto-retry без assignee.
- **DaDataClient** — `find_by_id(inn)` (точный поиск), `suggest(query)` (свободный поиск). 10k запросов/сутки бесплатно.
- **GiroClient** — публичный API `bo.nalog.gov.ru`, без авторизации. `get_summary(inn)` → выручка/активы по годам.
- Все сервисы инжектятся в dispatcher в lifespan (`main.py:131-139`), ровно 9: `dp["ai_client"]`, `dp["bitrix"]`, `dp["openrouter"]`, `dp["openclaw"]`, `dp["potok"]`, `dp["potok_frontend"]`, `dp["dadata"]`, `dp["giro"]`, `dp["userbot"]`.
  - `dp["userbot"]` легально бывает `None` (нет `TELETHON_SESSION` либо упал `start()`) — бот стартует «здоровым», рассылка кандидатам и авто-отказ молча мертвы.
- **ErrorMiddleware** + **AuthMiddleware** вешаются **внутри lifespan** (`main.py:144-147`), не в `create_dispatcher()`. Порядок: error wraps auth wraps handler. Только на `dp.message` и `dp.callback_query` — `channel_post` их НЕ проходит (отсюда свой try/except в `zabbix.py`).

### Router Registration Order (in `create.py`)

Order matters — `buffer.py` ВСЕГДА последний (catch-all):

1. start → 2. summarize → 3. meeting → 4. free_slots → 5. jira_task → 6. lead → 7. image → 8. ask_ai → 9. contract → 10. employee → 11. cicero → 12. socrates → 13. glafira → 14. recruiter → 15. stirlitz → 16. hudson → 17. zabbix → 18. group → 19. **buffer**

`work_router` в списке НЕТ — `work:*` ловится в `start.py`.

⚠️ ВАЖНО про `hint:*` callbacks: общий обработчик `F.data.startswith("hint:")` в `start.py` ловит ВСЕ hint-клики первым. Узкие хендлеры в роутерах-фичах перебить его НЕ могут. Любая новая кнопка-открыватель FSM должна быть зарегистрирована в `_simple_fsm_hints()` внутри `start.py`.

⚠️ `group_router` идёт **ПЕРЕД** `buffer_router`, и его `BotMentionFilter` останавливает propagation → сообщения с @упоминанием бота в `message_buffer` **не попадают** и в суммаризацию не идут.

⚠️ **FSM-состояния не отфильтрованы по типу чата**, ключ = (chat_id, user_id). Нажал «Спроси AI» в группе — следующее твоё сообщение в ЭТОЙ группе съедается ask_ai (он чистит state после первого же текста, дальше группа работает нормально). А вот **Цицерон и Марфа state НЕ чистят** — они едят все твои сообщения в этой группе до «◀️ Меню».

### Personas naming (исторический своп)

- `recruiter.py` (Potok.io scoring) → отображается как **«Глафира»** в UI (👔)
- `glafira.py` (OpenClaw office-manager) → отображается как **«Марфа»** в UI (🤖)
- Файлы/классы/callback-ID **НЕ переименованы** во избежание ломки. Только display strings в `start.py` и сообщениях.

### Authorization Flow

1. `/start` → Bitrix lookup `@username` (поле `BITRIX_TELEGRAM_FIELD`, default `UF_USR_1678964886664`)
2. Найдено → `(telegram_id, bitrix_user_id, display_name)` в `users`
3. `PUBLIC_COMMANDS = {"/start", "/help"}`, **`AUTH_COMMANDS = {"/summary"}`** — AuthMiddleware гейтит по факту ТОЛЬКО `/summary`.
4. ⚠️ **Остальное меню авторизацией не закрыто**: `ask_ai`, `image`, `contract`, `cicero`, `socrates`, `stirlitz`, `employee` не проверяют `db_user` вообще — доступны любому, кто нашёл бота, и жгут Claude/OpenRouter. Свои проверки есть у `glafira`/`recruiter`/`hudson` (allow-list по TG ID) и у «Моя команда»/«Мои встречи» (по `bitrix_user_id`). `meeting`/`free_slots`/`jira_task`/`lead` без `bitrix_user_id` просто нефункциональны.
5. `db_user` инжектится **не во ВСЕ** хендлеры: два ранних return до инжекта (PUBLIC_COMMANDS и не-slash в замьюченной группе). Безусловен только для CallbackQuery.
   - **Правило:** в message-хендлерах объявлять только как `db_user: DbUser | None = None`, иначе TypeError в замьюченной группе.

### MENU_KB (Inline Keyboard)

Defined в `start.py:24` — статическая константа, **без фильтрации по allow-lists при сборке** (гейт только на клике) → кнопки Хадсона/Глафиры/Марфы видны всем. Текущая раскладка:
- Сотрудник | Моя команда
- Встреча | Найди время
- Задача | Лид
- Мои встречи | Картинка
- Спроси AI | Суммаризация
- Проверь договор | Цицерон
- 🎓 Сократ | 🕵️ Штирлиц
- 🤖 Марфа | 👔 Глафира
- 🏠 Мисис Хадсон | ❓ Все команды

«Начать день в офисе/удалённо» из MENU_KB **удалены** (команда использует Bitrix24 check-in), но `HELP_TEXT` их всё ещё рекламирует, а `work:*` хендлер жив — по тапу в старое сообщение из истории фича отработает.

⚠️ `handle_hint` требует в сигнатуре `bitrix, potok, ai_client, bot, openrouter`. Забыл сервис в `dp[...]` → умирает **всё меню целиком**, а не одна фича.

### Ask AI

- Entry: «Спроси AI» → FSM `AskAI.waiting_for_question`
- `prompts/ask_ai_system.md` — персонаж «Джарвис Аркадия» (Digital Clouds), универсальный помощник
- Передаётся через `--append-system-prompt`
- Без agentic поведения (все tools выключены пустым whitelist)

### Stirlitz (B2B разведка)

- Entry: 🕵️ Штирлиц → FSM `Stirlitz.waiting_for_query`
- **Dispatcher** через Haiku (`prompts/stirlitz_intent.md`): пользовательский запрос (история до 6 сообщений) → JSON `{kind: company_inn|company_name|person|clarify, ...}`
- **`company_*`**: `DaData.find_by_id`/`suggest` + `GiroClient.get_summary` → JSON → Claude с `prompts/stirlitz.md`, `allowed_tools="WebSearch,WebFetch"`
- **`person`**: Claude с `prompts/stirlitz_person.md` — LinkedIn, Habr, VK, конференции
- **`clarify`**: FSM остаётся в waiting_for_query, бот задаёт вопрос → пользователь уточняет → Haiku видит обе реплики
- Краткие карточки → в чат; длинные (>4000 символов) → .md attachment. Промпты просят «~3500» — правишь лимит, трогай оба.
- Промпты грузятся **один раз на import** (`stirlitz.py:17-19`) — правка .md требует рестарта.
- ⚠️ **DC-контекст сюда НЕ подставляется**: в `prompts/stirlitz.md` нет плейсхолдера `{dc_context}`, единственная замена — `{data_json}`. Про DC там inline-однострочник; `stirlitz_person.md` не упоминает DC вовсе.
- ⚠️ **WebSearch/WebFetch фактически не выполняются** — см. Known Issues. Для `kind=person` это значит, что карточка собирается целиком из памяти модели.

### Sales Department Analytics

Полный аудит активности продажника из Bitrix24, на двух cron-расписаниях.

**Cron:**
- Дневной 19:00 (`SUMMARY_HOUR`), **только пн-пт** (`day_of_week="mon-fri"`)
- Недельный пятница 18:00 (**захардкожено в `main.py:183`**, не из env → в пятницу приходят оба)
- Джобы регистрируются только если заданы **ОБА** env (`SALES_REPORT_BITRIX_USER_IDS` + `SALES_REPORT_RECIPIENTS`) — иначе молча не зарегистрируются.

**Адресаты:** `SALES_REPORT_RECIPIENTS` (Telegram IDs через запятую; группы — с минусом, например `-4729014928`). Бот должен быть участником группы.

⚠️ **Пагинация обязательна**: Bitrix `*.list` отдаёт **максимум 50 строк за запрос**. Раньше все счётчики делали `len(result)` без пагинации → любая метрика с >50 записей молча упиралась в 50 (симптом: «в работе 50» у менеджера неделями как константа). Теперь: `_list_all()` идёт по `resp['next']` через все страницы (cap 40 стр = 2000, логирует при переполнении), `_list_total()` берёт точное число из поля `resp['total']` одним запросом. Любой новый `.list` — только через эти хелперы, НЕ `len(_safe_call(...).result)`.
- `_list_total` при отсутствии поля `total` **деградирует до `len(result)` первой страницы** → тихо возвращается к 50.
- `_safe_call` глушит исключение → `_list_all` отдаёт ЧАСТИЧНЫЙ список, `_list_total` — 0. Единственный след — строка в `activity.errors` (попадает в data_json промпта).

**Метрики (`sales_analytics.collect_user_activity`):**
- **Лиды**: `created` (за период, `_list_all`), `active` (`_list_total` — точное число) — фильтр `!STATUS_SEMANTIC_ID IN [S, F]`
- **Сделки**: `created`/`active`/`modified`/`won` (+`won_sum`)/`hot` (+`hot_sum`)/`avg_deal_age_days`
  - Whitelist воронок `cat_filter = {"CATEGORY_ID": allowed_cats}` (из `SALES_REPORT_DEAL_CATEGORIES`, default `27,31,33` — Услуги Б24, Общая, ПиК; исключает «Счета 1С» cat 0, «Продление Битрикс» cat 29, «Квал» cat 23) применяется КО ВСЕМ запросам сделок. Cat 0 — дубли-фантомы автодвижений 1С.
  - `deals_hot` = subset где **имя** стадии совпадает с `SALES_REPORT_ACTIVE_DEAL_PATTERNS` (default `кп,договор,счёт,счет,переговор,согласи,кэв провед,отработк,оплат`). Имена тянутся через `crm.dealcategory.stage.list`. **Переименование стадии в UI Bitrix молча обнуляет метрику.**
  - `deals_created` — не отдельный запрос, а подмножество `modified_deals`, т.е. только по разрешённым воронкам и только с DATE_MODIFY в периоде. Сравнение дат **лексикографическое** по ISO-строкам.
  - `deals_active` = `CLOSED="N"` + whitelist воронок, **без фильтра стадий** — «парковочные» стадии считаются активными. Это осознанный двухуровневый дизайн: active = вся открытая воронка, hot = то, что движется.
  - ⚠️ **`deals_won` за период и месячный факт считаются ПО-РАЗНОМУ** — см. Known Issues.
- **План/факт за календарный месяц**: `month_won_sum` + `monthly_plan` (env `SALES_REPORT_MONTHLY_PLAN`, default 220000₽). WON = `STAGE_SEMANTIC_ID=S` + `>=CLOSEDATE start_of_month` **+ `cat_filter`**. **`month_won_deals`** — расшифровка факта: per-deal `{id, title, amount, company, closedate}`, где `company` = юрлицо (`COMPANY_ID` через `crm.company.get`) или контакт (fallback на `CONTACT_ID`). Промпт печатает эти сделки под строкой «Факт».
  - Суммы складывают `OPPORTUNITY` **без конвертации валют** — сделка в USD прибавится к рублёвому плану как число.
- **Дела**: `activities_done` (`crm.activity.list`, по `RESPONSIBLE_ID`, к воронке не привязан), `stage_changes` через `crm.stagehistory.list` — N+1 fetch по каждой сделке (фильтра по user в API нет, только OWNER_ID=deal_id), sem(5). `deal_ids` = `modified_deals` + `active_deals` → автопереходы cat 0 в 🔄 переходы НЕ считаются.
- **Комментарии**: `crm.timeline.comment.list` (AUTHOR_ID=user_id)
- **Звонки** (voximplant.statistic.get):
  - Раздельно in/out/missed/callback по `CALL_TYPE` (1/2/3/4)
  - `entity_type`/`entity_name` резолвится через `crm.lead.get|contact.get|company.get|deal.get` (cached в `_enrich_calls`, **последовательно**)
  - **Транскрипция** (если `with_transcripts=True`): mp3 с Bitrix Disk через `disk.file.get` → Gemini 2.5 Pro `transcribe_voice(audio_format="mp3")` → Claude по `prompts/sales_call_analysis.md`. В промпт уходит `transcript[:6000]`, в `.md` — полный текст.
  - Параллельно sem(3), лимит max_transcripts=25 (топ по длительности). Звонки <15 сек не транскрибируются.
  - ⚠️ **Метрика без пагинации** — `len(calls_raw)` по первым 50 строкам (см. Known Issues). Формально не пагинируется и `crm.stagehistory.list` в `_count_stage_changes`, но там 50 строк = 50 переходов по ОДНОЙ сделке за период (на практике недостижимо); у звонков потолок упирается реально.
- `collect_for_user_ids` — `asyncio.gather` **без семафора**: между менеджерами нагрузка не ограничена.

**AI-отчёт** (`prompts/sales_summary.md`): подгружает `{dc_context}`, формат Telegram-HTML, блок «План/факт» с 🔴/🟡/🟢, конверсия WON/лиды, в конце — обращение «играющего РОПа» по имени. Формула 1+1+1+1: оценка → за что похвалить (обязательно) → конкретное действие на завтра → подбадривающая фраза. Жёстко при нулях, без оскорблений.

**.md-attachment**: `calls_transcripts_<N>d.md` со всеми разобранными звонками.

**Ручной запуск:**
```bash
docker compose exec bot python scripts/test_sales_report.py <bitrix_user_id> [days] [--no-send] [--date YYYY-MM-DD]
```
⚠️ `--date` (backfill за пропущенный день) работает **частично**: период-метрики его уважают, а план/факт и `avg_deal_age_days` считаются от `now`. Backfill за май, запущенный в июле, смешает майский период с июльским планом.

### Recruiter «Глафира» (Potok.io)

- Access control: `RECRUITER_ALLOWED` (TG IDs через запятую). Гейт **только на входе** — `recruit:stop`/`recruit:exit`/`recruit:noop` не проверяются.
- FSM: `Recruiter.choosing_job` → `confirming` → `scoring | contacting | inviting`. Storage — MemoryStorage; в FSM data лежат целые pydantic Job/списки Applicant → рестарт посреди рассылки убивает состояние.
- **Загрузка кандидатов**: `/api/v3/jobs/{id}/ajs_joins.json` (cursor pagination). Отсев `active=False` (рефузнутые/нанятые/архивные) делает `PotokClient._get_job_applicant_ids` по summary-ответу. Батчи по 5 + `sleep(0.5)` + sem 5 → вакансия на 300 откликов = минуты на клик.
- **Список вакансий**: `get_jobs` ходит с `per_page: 50` **без пагинации**, `_enter_recruiter` режет до `jobs[:20]` — вакансии за 20-й недостижимы.
- **Скоринг** (`resume_scorer.score_applicant`):
  - Claude (CLI-дефолт), промпт `prompts/recruiter_scoring.md` (загружается на import модуля) + опц. секция `Важно для CLAUDE:` (`extract_recruiter_instructions` забирает весь остаток описания, DOTALL)
  - Из job description вырезаются `Владельцы:` и `Ссылка для встречи:` (`_strip_admin_lines`) — Claude их не видит
  - Возвращает JSON: score 0-100, breakdown, strengths, weaknesses, **questions** (промпт просит **3-5**, валидации/паддинга нет)
  - Кэш вопросов в `_questions_cache: dict[applicant_id, list[str]]`
  - ⚠️ В промпте скобки JSON-примера **одинарные**: подстановка идёт регексом `\{(\w+)\}`. Переведёшь на `.format()` — промпт взорвётся.
- **Push в Potok**: HTML-комментарий + префикс `{score:03d}-` к фамилии + JARVIS-маркер `<!-- JARVIS:QUESTIONS:[...] -->`. Комментарий постится ПЕРВЫМ, префикс вторым, падение PATCH не пробрасывается → кандидат останется «новым» и получит второй комментарий на следующем прогоне.
- **Auto-promote** (`push_scoring`): при `score > POTOK_HIGH_SCORE_THRESHOLD` (default 80, **строгое `>`** — ровно 80 не двигает) и текущей стадии в `POTOK_HIGH_SCORE_SOURCE_STAGES` (default `Добавлен,Откликнулся`) → `move_applicant_to_stage(target=POTOK_HIGH_SCORE_STAGE)` (default `Контакт с рекрутером`)
- **Stage filtering**: `_filter_by_stage` (job_id, stage_name) для стадий «Интервью с рекрутером» и «Интервью с менеджером» (`MANAGER_INTERVIEW_STAGE_NAME`). Исключает **только `state_id != None`** — поля `active` у модели `AjsJoin` нет (это флаг `AjsJoinJob`, т.е. вакансии). Отсев рефузнутых делает `_get_job_applicant_ids`, а не этот фильтр.
- `move_applicant_to_stage` требует **ПЛОСКОГО** payload `{"stage_id": X}` без обёртки `ajs_join`. С обёрткой Potok возвращает 200 OK и ничего не меняет.

**Кнопка «📞 Связаться с кандидатами» (стадия «Интервью с рекрутером»):**
- Per-candidate карточка → «✉️ Написать» — intro («Я представляю компанию `RECRUITER_COMPANY` …») + вопросы (одним сообщением, нумерованные)
- «✉️ Написать всем» — bulk с throttle 1.5с, обрабатывает `FloodWaitError`/`PeerFloodError`, прогресс каждые 5
- Канал: Telegram через `userbot.send_to_user` → fallback **HH через PotokFrontendClient** (`accounts[].url ?t=<channel_id>` → POST `/client_api/jobs/{job}/{applicant}/communication/communicate.json`, обязательный `Referer` вида `{base}/j/{job}/all/a/{applicant}/`)
- ⚠️ **HH-фолбэк недостижим без телефона**: `if not phones: return "no_phone"` стоит ДО ветки HH. HH работает ровно в одном сценарии: телефон есть, но не резолвится в TG.
- HH-отправка — два независимых POST (intro + вопросы). Первый прошёл, второй нет → `hh_failed`, хотя intro доставлен; повторный клик пришлёт intro снова.
- ⚠️ **Отправка вопросов молча двигает стадию** на `POTOK_AFTER_CONTACT_STAGE` («Скриннинг резюме») БЕЗ `allowed_source_stages` → кандидат из «Интервью с рекрутером» уезжает **назад** в скрининг. Ошибка проглатывается.
- При успехе сохраняем `recruiter_contacts` (telegram_user_id → applicant_id, job_id). PK — `telegram_user_id`: кандидат на двух вакансиях теряет привязку к первой.
- Финальный отчёт по статусам (sent / sent_hh / no_phone / no_questions / no_channel / send_failed / hh_failed) с поимёнными списками

**Кнопка «📅 Пригласить на собеседование» (стадия «Интервью с менеджером»):**
- Парсит `Владельцы: @vasya,@petya` и `Ссылка для встречи: https://...` из описания вакансии (`_extract_owners`, `_extract_meeting_link`) — регексами по уже расплющенному через `_strip_html` тексту
- Резолвит имена владельцев через `bitrix.find_user_by_nickname`
- Шлёт через Telegram (userbot) приглашение с ссылкой на Bitrix calendar-sharing
- Pre-validation: нет ссылки в описании → ошибка с предложением добавить

**Auto-reject входящих ответов кандидатов:**
- В `main.py _on_candidate_reply` (на `userbot.set_reply_handler`) для каждого входящего:
  1. Lookup `recruiter_contacts` по `sender_id`
  2. `potok.post_candidate_reply` — комментарий в Potok («❓ Заданные вопросы:» + «💬 Ответ кандидата:»)
  3. `classify_rejection_intent(text, ai_client)` по `prompts/rejection_classifier.md` → {score, reasoning}. Fail-safe: любая ошибка → `score=0`.
  4. Если `score > REJECTION_CLASSIFIER_THRESHOLD` (default 70) → `potok.set_applicant_active(active=False)` + audit-комментарий
- ⚠️ Хендлер висит на **ВСЕХ** входящих личного аккаунта и фильтрует лишь по `sender_id` — отслеживаемый кандидат написал в общей группе, и его реплика уедет комментарием в Potok и пройдёт классификатор.

### Мисис Хадсон (Weekly P&Q Analyst)

Еженедельный аудит worklog'ов отдела Production&Quality. Cron Пн 11:00 Нск. Регистрируется **безусловно** — отключить через .env нечем (`HUDSON_SKIP_JIRA` гасит только Jira; `HUDSON_CHAT_ID=0` — только групповой пост, DM уйдут).

**Что считает**:
- Набор проектов аудита = `dcj_projects` где `direction = "WEB - ПиК"` (287 из 638 в DCJ.xlsx) **плюс** `EXTRA_AUDIT_PROJECTS` (`hudson_analyzer.py`) — **авторитетный оверлей**: его `is_internal` **перебивает** `dcj_projects` (`_load_web_pik_projects` делает `result.update(...)`) → классификация зафиксирована в коде и переживает пере-импорт DCJ.xlsx. Сейчас: `COZYHOME`/`MZNN` (внешние), `DCNEW` (внутренний, АУП); `DA`/`HRD`/`SHR` (внутренние, АУП); `TSAI`/`TSTM`/`TSS`/`EA` (внешние); `DFSH`/`VMP`/`RMED`/`VIN` (внешние). Добавить/переклассифицировать — допиши `ключ: 0|1` (0 внешний / 1 внутренний), читается на лету, DB-сид не нужен. Кандидаты приходят в `unknown_projects_<period>.md`.
- Для каждого разработчика из `hudson_managers` (4 менеджера / 15 разработчиков) — Jira worklog за неделю через `services/jira_worklog.py`. **Тянем БЕЗ фильтра по проектам** (`project_keys=None`), потом бьём на in-scope / out-of-scope — иначе часы вне набора молча выпадали (жалобы «мой лог не учтён»).
  - ⚠️ `_load_devs` берёт `WHERE jira_username IS NOT NULL` — разработчик без резолвнутого логина выпадает целиком (ни строки в отчёте, ни в `all_worklogs.md`, ни Jira-задачи). Warning только если логина нет у ВСЕХ 15.
  - `jira_username` в этом Jira Server физически **равен email**. JQL: `worklogAuthor in ("...@dclouds.ru")`. Не «чинить» на «настоящий username».
- Суммирует часы → `total / internal / external` (internal по `is_internal=1`) **только по in-scope**. Часы вне набора → `out_of_scope_entries` / `out_of_scope_hours` (в total НЕ идут, только в .md-сверку).
- **Простой (bench)** — отдельная корзина `downtime_*`. Проекты простоя (`DOWNTIME_PROJECT_KEYS_FALLBACK = DCBE/DCFE/DCAP/DCQQ/DCDE` + детект по имени `Простой %`). В DCJ.xlsx они `is_internal=1` → раньше валились во внутренние часы (ложный 🔴 у людей на скамейке). Теперь проверяются ПЕРВЫМИ, идут в `total_hours`, но НЕ в internal/external и НЕ в bad-comment классификацию. Отдельная строка `💤 простой Xh` + .md + Jira-задача.
  - **DCDE** в `dcj_projects` имеет `direction='Admin&DevOps'` — попадает только через `LIKE 'Простой %'` или fallback-набор.
- Норма по ТК РФ (per-dev, в `build_reports`):
  - База `WEEKLY_HOURS_NORM=32h`, `HOURS_PER_DAY=8h`. Минус `HOURS_PER_DAY` за каждый рабочий (Пн-Пт) праздник (`services/holidays_api.py` — `isdayoff.ru`, кэш в памяти; кэшируется и код ошибки → осечка API делает день рабочим до рестарта).
  - **Неполная ставка** — `PART_TIME_NORMS: dict[developer_pattern → (норма, часов-в-дне)]`. Сейчас `Осицын → (25.0, 5.0)`. Полная = `(32, 8)`. Ключ = `developer_pattern`, читается на лету.
  - Минус «вес дня» за каждый рабочий день отпуска/больничного/командировки (`bitrix.get_absences()`).
  - `weekly_norm <= 0` → `on_leave=True`.
  - `compute_weekly_norm()` — хелпер «норма полной ставки», build_reports его не зовёт.
  - ⚠️ **Двойной вычет** праздника внутри отпуска — см. Known Issues.
  - `PART_TIME_NORMS` и `EXTRA_AUDIT_PROJECTS` матчатся **по точному ключу**. Опечатка = тихий откат на 32h или в out-of-scope.
- Плохие комменты — **Haiku через OpenRouter** (`anthropic/claude-haiku-4.5`, не Claude CLI — 150+ вызовов на прогон сожгут квоту), классификатор `prompts/hudson_bad_comment.md` (мягкий: ≤30мин комменты «дейлик/викли» норма). Sem(5), 60с timeout + 1 повтор. **Fail-open**: после 2 неудач запись просто не попадает в `bad_comments` → счётчик тихо занижается при флапах OpenRouter.

**Рассылка** (`services/hudson_notifier.py`):
- Менеджерам (DM): краткий per-dev summary (`🔴 Гусев: 32.0h/<b>17.2h</b> (внутр выше 8h)` + счётчик плохих коммов). Уходит только если менеджер есть в `users` (прошёл `/start`).
- Алине Васьковой (`HUDSON_DEPT_HEAD_BITRIX_ID=37`) — **она РОП P&Q И одновременно полноценный менеджер** (`"Васькова": ["Скородумов", "Маврин"]`). Без дедупа попадает и в цикл DM-менеджерам, и в ветку РОПа: тексты РАЗНЫЕ (её команда / весь юнит), а **.md дублируются** — 8-10 файлов вместо 4-5. Замысел «играющий РОП».
- В группу `HUDSON_CHAT_ID`: шапка с тэгами менеджеров + AI-мотивашка от Claude CLI (1 вызов/нед). Промпт мотивашки **хардкодит «4 менеджера» и «норму 32h»** — не пересчитывается при смене маппинга/part-time.
- Всем — **4 .md аттачмента** (+ 5-й опционально). ⚠️ **Файлы одинаковые для ВСЕХ** — каждый менеджер получает данные по всем командам, включая чужие плохие комменты. Персонализирован только текст DM.
  - `bad_comments_<period>.md` — плохие комменты с кликабельными Jira-ссылками
  - `internal_hours_<period>.md` — внутренние часы по задачам, per-dev
  - `downtime_<period>.md` — простой (часы + issue + коммент, для приёмки менеджером)
  - `all_worklogs_<period>.md` — **ВСЕ** worklog'и для сверки, разбито на «✅ Учтено» / «💤 Простой» / «⚠️ Не учтено (проект вне аудита)» с ключом проекта
  - `unknown_projects_<period>.md` — **только если есть неизвестные проекты**. Агрегирует out-of-scope по `project_key`: ключ + название + часы + разработчики + пример задачи + готовая строка `"KEY": 0,  # name` для копипаста в `EXTRA_AUDIT_PROJECTS`.
- Полные данные в .md, в Telegram только сводка (`TG_MAX=3800` + `_split_block` режет по строкам).

**Jira-задачи в проекте `PQ`** (`PQ_PROJECT_KEY` — хардкод; если `HUDSON_SKIP_JIRA=false`):
- «Подтвердить внутренние часы» — **только если есть разработчики с `internal_hours > 0`**. Assignee = `hudson_managers.manager_jira_username`. Description: per-dev разбивка внутренних часов + список плохих коммов.
  - ⚠️ Плохие комменты попадают в Jira **только внутри этой задачи** → у менеджера, чья команда неделю отработала целиком по внешним проектам, они не уедут никуда, кроме .md.
- «Принять простой» — на менеджера, у кого есть `downtime_hours > 0`.
- «Отгул: <разработчик>» — если `is_under_norm`.

**Кнопка «🏠 Мисис Хадсон» в меню**:
- Доступ через `HUDSON_ALLOWED` (TG IDs), гейт на клике (`start.py:375` → `hudson.py:34`).
- Полный отчёт **в личку нажавшему**. Сообщения + 4 .md (+ `unknown_projects` если есть).
- Если DM закрыт — пишет в группу что надо открыть `/start` в личке.
- ⚠️ **Период — скользящее окно последних 7 дней** (`until = вчера`, `since = until - 6`), НЕ календарная Пн-Вс. Cron использует ту же формулу — Пн-Вс выходит из расписания, а не из расчёта. Клик в среду даёт Ср-Вт и не совпадёт с понедельничной рассылкой. Настоящую календарную неделю считает только `run_hudson_now.py:_last_full_week()`.

**Маппинг менеджер↔разработчики**:
- В `services/hudson_repo.py:DEFAULT_MANAGER_MAPPING` (hardcoded, потому что в Bitrix у групп разработки head=NULL).
- 4 менеджера / 15 разработчиков. Реорг 2026-06: Даниленко упразднён, его люди разведены (Геливанов/Присяжнюк → Бешеля; Овсянников/Осицын/Сердюков/Ушаков → Кузнецова Юлия). Ключ менеджера может быть «Имя Фамилия» — резолв разбивает на слова.
- Seed резолвит Bitrix ID/email/full_name/jira_username — `_find_user_by_last_name` (LAST_NAME filter + LIKE fallback, потому что у Осицына в Bitrix `LAST_NAME='Осицын '` с trailing-пробелом).
  - ⚠️ Берёт **ПЕРВОГО активного**. Однофамильцы резолвятся молча и неверно (`%Кузнецов%` матчит и Кузнецова, и Кузнецову Юлию).
- **Seed идемпотентен**: после upsert реконсиляция — удаляет пары, которых больше нет в маппинге. Возвращает `(upserted, removed_pairs, warnings)`.
- Запуск: `scripts/init_hudson_db.py` — парсит `DCJ.xlsx` и сидит маппинг. **Менять маппинг → перезапустить на проде**, иначе `hudson_managers` останется старой.
- ⚠️ **Пустой `dcj_projects` прогон НЕ останавливает**: `_load_web_pik_projects` всегда доливает `EXTRA_AUDIT_PROJECTS` (12 ключей) поверх выборки из БД → проверка `if not projects` никогда не срабатывает, а warning «dcj_projects не содержит WEB-ПиК проектов» — мёртвый код. Вместо тихого выхода аудит схлопнется до 12 EXTRA-проектов: все остальные часы уедут в out-of-scope, все разработчики окажутся под нормой, и каждому менеджеру прилетит пачка задач «Отгул». Забыл прогнать `init_hudson_db.py` на свежей БД — получишь именно это, а не пустой отчёт.

**Скрипты**: `run_hudson_now.py` (`--offset N`, `--since/--until`, `--dry-run`), `test_hudson.py` (console dry-run).

### Zabbix Monitor

- **Real-time** (`bot/routers/zabbix.py`): `@router.channel_post(F.chat.id == settings.zabbix_channel_id, F.text)` парсит сообщения Zabbix-бота регексами (🔴 = открытие, 🟢 = закрытие; key = `Original problem ID`). UPSERT в `zabbix_problems`.
  - 🔴/🟢 должен быть **ПЕРВЫМ символом** после `lstrip()`. Любой префикс → alert молча игнорируется **без лога**. Нет `Original problem ID:` → тоже молча дроп.
  - `upsert` в ON CONFLICT **не обновляет `opened_at`** — повторные 🔴 не сдвигают отсчёт 24ч.
- **Cron 10:00** (`zabbix_check_unresolved_job`): проблемы где `resolved_at IS NULL AND jira_task_key IS NULL AND opened_at <= now-24h`, фильтр по severity (`ESCALATING_SEVERITIES = {Warning, Average, High, Disaster}`), создаёт задачу в `ZABBIX_JIRA_PROJECT` (default `DA`), маркирует `jira_task_key`.
  - `set_zabbix_jira_key` вызывается ПОСЛЕ `create_issue` — Jira ответила, SQLite упал → дубль на следующем прогоне.
  - ⚠️ Сравнение severity **регистрозависимое, без нормализации** — см. Known Issues.
- **Backfill**: `scripts/scan_zabbix_month.py` — **Telethon-userbot** (обычный бот не умеет читать историю каналов), 30-дневный скан + задачи по всему открытому.

### B24 Lead Recon (b24.xlsx → CRM лиды Косте)

Одноразовый перевод базы из `b24.xlsx` (вкладка «Клиенты», ~4500 строк) в B24 CRM как leads на Костю Карачева (`bitrix_id=697`), источник «База Яндекс».

**Конвейер** (`scripts/b24_lead_from_xlsx.py`):
1. По каждому домену — Claude CLI с `allowed_tools="WebSearch,WebFetch"`, промпт `prompts/b24_lead_recon.md` — JSON-карточка (company_name, отрасль, регион, контакты, новости, гипотезы болей, top-3 конкурента, industry_dynamics).
2. WebFetch виснет/Cloudflare (180с timeout) → **fallback** на `prompts/b24_lead_recon_searchonly.md` (90с). Сайт помечается `site_unreachable=true`, лид всё равно создаётся.
3. Лид через `crm.lead.add`. **Полный recon** → **timeline.comment** через `bitrix.add_timeline_comment()`. В `COMMENTS` — однострочный summary (виден в канбане).
4. Контакты: телефоны/emails в `PHONE`/`EMAIL`. Telegram/WhatsApp из соцсетей — в `IM`.

⚠️ **`SOURCE_ID` не захардкожен** — резолвится по **отображаемому имени** «База Яндекс» через `crm.status.list` (ENTITY_ID=SOURCE). Переименование/удаление опции в UI ломает резолв → `source_id or "OTHER"`, прогон продолжится и все лиды уедут с OTHER (одно предупреждение в начале прогона на 4500 доменов теряется). Этот инцидент и породил `b24_lead_set_source.py` (он на том же месте делает `sys.exit`).

**Бэкфилл UF-полей** (`scripts/b24_lead_backfill_fields.py`) — отдельный проход, БЕЗ Claude:
- `UF_CRM_1779947430020` (string) — **Агентство Текущее** ← xlsx колонка C
- `ADDRESS_CITY` — **Город** ← regex «Регион: …» из COMMENTS
- `UF_CRM_1779951351014` (enum, 18) — **Сфера** ← `classify_industry()` по keywords (см. `INDUSTRY_GROUPS`)
- `UF_CRM_1779947540127` (boolean Y/N) — **Есть сотовый** ← regex `^[78]?9\d{9}$` по PHONE
- `UF_CRM_1779947613495` (enum 4) — **Бюджет на рекламу** ← bucket xlsx revenue (`«до 500.000»`/`«500.000-1.000.000»`/`«1.000.000-3.000.000»`/`«Выше 3.000.000»`)

`FIELD_OVERRIDES` хардкодит UF-коды и **перекрывает** auto-match по title. `--discover` показывает фактическое состояние. Значения нет в enum B24 → поле молча пропускается.
⚠️ `--limit` (default 10) действует **только** для `--dry-run` (`sample = leads[:args.limit] if args.dry_run else leads`); ветка `--industries` идёт по всем лидам и флаг игнорирует (argparse-help «при --dry-run / --industries» устарел); запуск без флагов обновляет ВСЕ лиды.
⚠️ Фильтрует по `%SOURCE_DESCRIPTION="Recon из b24.xlsx"` → **retail-лиды не затрагивает**. Парсит регексами `Отрасль:\s*([^.]+)\.` / `Регион:` — жёсткая завязка на `_build_short_summary`.

**Чекпоинт-стейт** (`data/b24_processed.json`, volume-mounted): домен → `status: ok/skip_recon/error` + `lead_id`, `timeline_id`, `site_unreachable`, `ts`. При перезапуске пропускает все 3 статуса.

**Восстановление** (`scripts/rebuild_b24_state.py`): `crm.lead.list` по `%SOURCE_DESCRIPTION`, парсит домен из `WEB[0]` или TITLE. Использовался когда обнаружили, что state писался в /app/ (ephemeral) — было 1000+ лидов при 108 в state.

**Запуск на сервере**:
```bash
screen -S b24 -dm bash -c 'docker compose exec -T bot python scripts/b24_lead_from_xlsx.py --all > data/b24_run.log 2>&1'
```
~2.5 мин/лид. 4500 доменов = ~7 суток (subscription weekly quota Claude Max сжирается за пару дней).

### Retail Lead Recon (retail.xlsx → лиды Добрякову)

Второй, **независимый** конвейер — импорт контактов с выставки. `scripts/retail_lead_from_xlsx.py`.

- Вход: `retail.xlsx` (лист `Sheet1`, колонки last/first/company/position/email/phone/tg) → лид на **Сергея Добрякова** (`DOBRYAKOV_BITRIX_ID = 3833`)
- Источник: `LEAD_SOURCE_NAME = "С мероприятия"` через `crm.status.list`, fallback-хардкод `LEAD_SOURCE_ID_FALLBACK = "UC_3NJ3TZ"`; `SOURCE_DESCRIPTION = "Выставка (retail.xlsx)"`
- Промпты: `prompts/retail_lead_recon.md` (180с, WebSearch+WebFetch) → fallback `retail_lead_recon_searchonly.md` (90с)
- Чекпоинт: `data/retail_processed.json`, ключ `email.lower()` иначе `company|last|first`.lower()
- Флаги: `--all/--limit/--dry-run/--force/--require-recon` + недокументированные `--row N` и `--recon-file`

**Отличия от b24, которые важны:**
- **Контактные поля берутся ИЗ ФАЙЛА**, Claude обогащает только профиль компании
- Сайта в файле нет — определяется по домену корп. почты (`_site_hint`, отсев `FREE_EMAIL_DOMAINS`) или WebSearch
- **Лид создаётся ВСЕГДА**, даже при пустой разведке (b24 при пустой ставит `skip_recon` и лид не создаёт). `--require-recon` меняет это, но делает `continue` **без записи состояния** → на следующем прогоне снова pending.
- `_norm_tg` отдаёт handle только для `[A-Za-z0-9_]{4,32}` — «Lyibov Milshteyn» распознаётся как имя и в IM не пишется
- ⚠️ Пишет **BBCODE в timeline.comment** (`[B]=== КОНТАКТ С ВЫСТАВКИ ===[/B]`), что расходится с правилом ниже («в timeline.comment — обычные `\n`»)

### Socrates (Meeting Analyser)

- Entry: 🎓 Сократ → FSM `Socrates.waiting_for_url`
- Per-user `asyncio.Lock` против параллельных пайплайнов (гонка: `if user_lock.locked()` и `async with user_lock` не атомарны; `_USER_LOCKS` никогда не чистится)
- SSRF guard: DNS resolution + private-address blocklist на **каждый** redirect hop. Редиректы вручную: `follow_redirects=False` + цикл ≤5 хопов. **Включишь `follow_redirects` — дыра.**
- URL only (Telegram cap 20 MB на приём файлов). Поддерживаются Yandex.Disk, **Google Drive** (`?id=`/`/file/d/{ID}/view` → rewrite на `drive.usercontent.google.com/download?...&confirm=t`), прямые URL. `_is_google_drive` НАМЕРЕННО не включает `drive.usercontent.google.com` — иначе резолвер зациклится на переписанном URL.
  - Тексты подсказок (`start.py:257`, `socrates.py:81`) Google Drive **не упоминают**, хотя он поддерживается.
- `MAX_DOWNLOAD_BYTES = 1 GiB` (хардкод в `meeting_downloader.py:35`, не в .env). Проверка по `Content-Length` и по факту в потоке; частичный файл удаляется.
- Content-Type check после редиректов — если text/html, abort
- ffmpeg → mono 16kHz opus 24kbps; `MEETING_MAX_MINUTES` (default 90). `_ffprobe_path()` выводит ffprobe из `FFMPEG_BIN` через `Path.with_name` — кастомный бинарь требует ffprobe **рядом**.
- Gemini (диаризация) → Claude×2 (review + brief). 3 артефакта `.md`
- **Чанковая транскрипция длинных записей** (`meeting_pipeline._transcribe`, v4.39.0): Gemini не проглатывает длинную запись одним запросом — виснет и падает с `provider_overloaded (timeout)`. Записи длиннее `_CHUNK_IF_LONGER_THAN` (22 мин) режутся `ffmpeg_tool.split_audio` (stream-copy, `-f segment`) на куски по `_CHUNK_SECONDS` (20 мин), транскрибируются **последовательно** (параллель снова словит overload), сегменты склеиваются со смещением таймкодов `i * _CHUNK_SECONDS`, `speakers_count` = max по кускам. Короткие (≤22 мин) — один запрос.
  - **Реальное окно чанкинга — 22-90 минут**: всё длиннее отбивается `MEETING_MAX_MINUTES` до `process_meeting` → кусков максимум 5.
  - ⚠️ Провал `split_audio` **тихо откатывается** на один запрос — см. Known Issues.
  - ⚠️ **Лейблы спикеров НЕ ремапятся между чанками** — см. Known Issues.
  - Хвостовой кусок может быть крошечным (n считается по ОЦЕНКЕ) → на тишине Gemini вернёт пусто → падает ВСЯ транскрипция.
  - `meeting_pipeline.py:16` тянет `_build_full_text` из `openrouter_client` **по приватному имени**.
- OpenRouter: **retry один раз** при `provider_overloaded` (HTTP 200 + embedded 503 in choice payload) и любом 5xx через `_transcribe_once` → `TranscriptionResult(retryable=True)` → wrapper повторяет через 5с.
  - **НЕ retryable**: пустой content, refusal, провал `parse_json_response` — сразу финал.
  - `choice.get("error")` проверяется ТОЛЬКО при пустом content → вернул и текст, и error → error молча игнорируется.
- Бюджеты времени нигде не сходятся в общий дедлайн: ffmpeg 1800с, транскрипция до 300с × 2 попытки × 5 кусков (~50 мин), review + brief по 600с. Общего таймаута на пайплайн нет.

### Daily Summary / Wed Frog / Mon Poster

Тела — в `scheduler/jobs.py`, триггеры — в `main.py`:
- `daily_summary` — `SUMMARY_HOUR:SUMMARY_MINUTE` (19:00), **без `day_of_week`** → работает и в выходные. Регистрируется безусловно.
- `wednesday_frog` — `CronTrigger(day_of_week="wed", hour=10)` (**хардкод**, через env только chat_id). Рандомный стиль из `FROG_STYLES`.
- `monday_poster` — `hour=9` (**хардкод**). Конструктивистский плакат с IT-героем.
- Коллизии: 19:00 — `daily_summary` + `sales_dept_summary_daily` (тяжёлый, с транскрипцией). Среда 10:00 — `zabbix_check` + `wednesday_frog`.

## Database Schema (aiosqlite)

9 таблиц. `schema_version` создаётся **внутри `_run_migrations()`**, а не в константе `SCHEMA` — при беглом чтении не видна.

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

-- Мисис Хадсон — справочник проектов из DCJ.xlsx (638 проектов, 287 в WEB-ПиК)
dcj_projects (
    project_key PK, name, is_internal, direction, category, updated_at
)

-- Мисис Хадсон — маппинг менеджер↔разработчики (DC-specific, в Bitrix у групп head=NULL)
hudson_managers (
    manager_name + developer_pattern PK,
    manager_bitrix_id, manager_jira_username, manager_full_name,
    developer_bitrix_id, developer_email, jira_username
)

-- Версия схемы (создаётся в _run_migrations, не в SCHEMA)
schema_version (version INTEGER NOT NULL)
```

⚠️ **Контракт миграций** (`db.py:106,124`): `for i, sql in enumerate(MIGRATIONS, start=1): if i > current`. Версия = **ПОЗИЦИЯ в списке** → дописывать можно **ТОЛЬКО в конец**. Вставка в середину сдвинет индексы: на прод-БД новая миграция молча не применится, на новых применится не то. `_run_migrations` глушит `OperationalError` только с текстом `duplicate column`; любая другая ошибка роняет старт.

⚠️ `db.py:108` (миграция №2) хардкодит `INSERT OR IGNORE INTO muted_groups VALUES (-1001408128567)` — нарушение «No hardcoded user IDs»; на свежей БД мьют воскресает.

⚠️ `users.is_active` нигде не выставляется в 0 — только `DEFAULT 1` и чтение в `get_active_users`. Деактивировать пользователя штатно невозможно.

## Config (.env)

⚠️ **`extra='forbid'`** (дефолт pydantic-settings при `model_config` без явного `extra`): любой лишний **непустой** ключ в файле `.env` роняет старт с `extra_forbidden`. Лишние переменные из ОС-окружения игнорируются.
⚠️ **`.env.example` протух и НЕ копируется** — `cp .env.example .env` даёт `ValidationError` (`OPENAI_MODEL`, `BASE_URL` — `extra_forbidden`). Последний раз трогали 198 коммитов назад; отсутствуют 14+ актуальных переменных.

### Required
- `BOT_TOKEN` — Telegram BotFather

### AI
- `CLAUDE_CODE_OAUTH_TOKEN` — **обязателен**. Long-lived (из `claude setup-token`, ~1 год) — рекомендуемый режим.
- `CLAUDE_REFRESH_TOKEN` — **наличие включает legacy auto-refresh**. Для long-lived убрать/оставить пустым.
- `CLAUDE_CLI_PATH` (default `claude`)
- `CLAUDE_MODEL` — опц. override. По умолчанию `""` → флаг `--model` не передаётся → CLI-дефолт (Sonnet).
- `CLAUDE_OAUTH_CLIENT_ID` — default official Claude Code ID
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (default `google/gemini-2.5-pro`), `OPENROUTER_TIMEOUT` (default 300s)

### Bitrix24
- `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `BITRIX_DOMAIN`, `BITRIX_REFRESH_TOKEN` (используется только когда `data/bitrix_tokens.json` нет)
- `BITRIX_TELEGRAM_FIELD` (default `UF_USR_1678964886664`)
- `BITRIX_EMAIL_GUESTS_SCAN_MAX` (2000), `BITRIX_EMAIL_GUESTS_MULTIPLIER` (3)

### Potok.io
- `POTOK_API_TOKEN`, `POTOK_BASE_URL` (default `https://app.potok.io`)
- `POTOK_AFTER_CONTACT_STAGE` (default `Скриннинг резюме` — опечатка на стороне Potok, так и надо)
- `POTOK_HIGH_SCORE_THRESHOLD` (80), `POTOK_HIGH_SCORE_STAGE` (`Контакт с рекрутером`), `POTOK_HIGH_SCORE_SOURCE_STAGES` (`Добавлен,Откликнулся`)
- **Frontend session** (для HH-messaging через `/client_api/*`):
  - `POTOK_FRONTEND_ACCESS_TOKEN`, `POTOK_FRONTEND_CLIENT`, `POTOK_FRONTEND_UID`
  - Извлекаются из браузерной сессии (DevTools → Network → headers любого запроса на app.potok.io). Не ротируются.

### Userbot (Telethon — для рекрутёра и Zabbix backfill)
- `TELETHON_API_ID`, `TELETHON_API_HASH` (one-time с my.telegram.org)
- `TELETHON_SESSION` — StringSession, сгенерировать через `scripts/create_userbot_session.py`
- `RECRUITER_COMPANY` (default `Digital Clouds`), `RECRUITER_NAME` — для intro в первом сообщении кандидату

### Sales analytics
- `SALES_REPORT_BITRIX_USER_IDS` — Bitrix IDs продажников через запятую
- `SALES_REPORT_RECIPIENTS` — TG IDs кому слать (можно группы с минусом). **Оба обязательны, иначе джоб не регистрируется.**
- `SALES_REPORT_MONTHLY_PLAN` (default `220000` ₽)
- `SALES_REPORT_DEAL_CATEGORIES` (default `27,31,33`) — какие воронки учитывать
- `SALES_REPORT_ACTIVE_DEAL_PATTERNS` (default `кп,договор,счёт,счет,переговор,согласи,кэв провед,отработк,оплат`) — паттерны имён стадий для «горящих»
- ~~`SALES_REPORT_HOT_STAGES`~~ — **мёртвая**: объявлена в `config.py`, не читается нигде.

### Zabbix
- `ZABBIX_CHANNEL_ID` (numeric, начинается с `-100`)
- `ZABBIX_JIRA_PROJECT` (default `DA`)
- `ZABBIX_THRESHOLD_HOURS` (default 24)

### Мисис Хадсон
- `HUDSON_DEPT_HEAD_BITRIX_ID` (default `37` — Алина Васькова, РОП P&Q; она же полноценный менеджер)
- `HUDSON_ALLOWED` — TG IDs кому доступна кнопка «🏠 Мисис Хадсон»
- `HUDSON_CHAT_ID` — общая группа отчёта. Бот должен быть в группе. `0` подавит только групповой пост.
- `HUDSON_SKIP_JIRA` (default `false`) — `true` = Telegram-рассылка работает, Jira-задачи не создаются.

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
- **Все ALLOWED-списки парсятся на import модуля**, как и фильтр `F.chat.id == settings.zabbix_channel_id`.

### Scheduled content
- `WEDNESDAY_FROG_CHAT_ID` (default 0 = disabled), `MONDAY_POSTER_CHAT_ID` (0). Времена (Ср 10:00 / Пн 9:00) — хардкод.

### Socrates
- `FFMPEG_BIN` (`ffmpeg`), `MEETING_MAX_MINUTES` (90)

### Webhook
- `WEBHOOK_TOKEN` — при пустом (дефолт) оба вебхука отвечают **503**, т.е. выключены.

### Other
- `DB_PATH` (`data/arkadyjarvis.db`), `SUMMARY_HOUR` (19), `SUMMARY_MINUTE` (0), `TIMEZONE` (`Asia/Novosibirsk`)

⚠️ **Правка .env требует `docker compose up -d --force-recreate`**, не `restart`/`exec`: docker `env_file` перекрывает pydantic `.env`, а многие значения читаются на import модуля.

## Coding Guidelines

- **No hardcoded field IDs** — Bitrix UF_* в `config.py`
- **No hardcoded user IDs** — access lists в `.env`
- **No hardcoded secrets** — `.env` через pydantic-settings
- **JSON from AI** — `utils.parse_json_response()`
- **OpenClaw isolation** — всегда `user_id` в `openclaw.stream_chat()`
- **Lead creation** — `SOURCE_ID`/`SOURCE_DESCRIPTION` + creator's Telegram contact в COMMENTS
- **HTML-escape user strings** — всегда `html.escape()` для user-controlled (имена, компании, AI-output). Telegram default parse_mode = HTML.
- **Длинные AI-ответы — два разных паттерна**:
  - Интерактивные ответы в FSM-роутерах (Штирлиц, Цицерон, договор) → `.md` attachment при >4000 символов
  - Broadcast/cron-отчёты (sales, Хадсон) → чанк по строкам через `utils.split_telegram_html(text, limit=4000)` или `_split_block` (`TG_MAX=3800`). Режут строго по `\n` — многострочный `<b>` порвётся.
- **Telegram-фолбэк на `.md`** — `contract.py:_send_as_md` ловит не только длину, но и `TelegramBadRequest` («can't parse entities» от грязного markdown Claude). В `cicero.py` такого фолбэка **нет** — при добавлении длинных ответов копируй паттерн из contract.
- **AIClient injection** — сервисы получают `ai_client` как параметр, не создают свой
- **DB user в callbacks** — использовать middleware-инжекцию `db_user`, не fetcher. В message-хендлерах — только `db_user: DbUser | None = None`.
- **Prompts in `prompts/`** — добавил `.md` → `load_prompt(name)`. Placeholders типа `{data_json}` / `{dc_context}` подставляет caller через `.replace()`. `load_prompt` с точкой в имени пробует только точный путь (fallback на .md/.txt не работает).
- **CLI tools security** — `allowed_tools` = **whitelist** в `--tools`. Пробрасывать ТОЛЬКО read-only (WebSearch/WebFetch). НИКОГДА Bash/Read/Write/Edit — это RCE. Не возвращать deny-list.
- **CLI cwd=/tmp** — иначе Claude CLI подхватит project CLAUDE.md как system context.
- **Haiku для дешёвых классификаторов** — передавать **явно**: Claude CLI → `model="claude-haiku-4-5-20251001"` (полный ID, alias `haiku` может не разрешиться); OpenRouter → `anthropic/claude-haiku-4.5`. Без явного `model=` уйдёт CLI-дефолт (Sonnet).
- **Bitrix stage filtering** — для лидов `STATUS_SEMANTIC_ID` (`S`/`F`/null). Для сделок — `CATEGORY_ID` (whitelist воронок) + опц. NAME pattern на стадиях. `CLOSED=N` недостаточно — есть кастомные «парковочные» стадии с семантикой `P` но фактически не активные. **Для WON — `STAGE_SEMANTIC_ID=S`, а не суффикс `STAGE_ID`.**
- **N+1 в API** — для `crm.stagehistory.list` и подобных нет фильтра по user, нужен N+1 fetch по `OWNER_ID=deal_id`. Параллелить через `asyncio.Semaphore(5)`.
- **callback_data — 64 БАЙТА**, не символа. Кириллица = 2 байта/символ.

## Running

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Health: `curl localhost:8001/api/health` — **всегда 200**, даже при `status: "degraded"`. Для liveness парсить тело.

Docker: `docker compose up --build` (port 8002), logs: `docker compose logs -f bot`

Версия — только `app/version.py` (git-тегов нет).

## Known Issues

### Подтверждённые баги (не «так задумано»)

- 🔴 **WebSearch/WebFetch у Claude CLI фактически НЕ выполняются.** `--tools` даёт инструменту *доступность*, но не *разрешение* — для разрешения нужен `--allowedTools`, которого в репо нет. Плюс `cwd="/tmp"` отбрасывает проектный allow-list. Воспроизведено на CLI 2.1.210: из /tmp с прод-argv → «WebSearch отклонён (требуется разрешение)»; с `--allowedTools` → работает; из корня проекта → работает. **Это НЕ регрессия v4.33.0** — из /tmp поиск не работал никогда, argv эпохи `--disallowed-tools` отклоняется так же. Фикс — добавить `--allowedTools` рядом с `--tools`, не откатывать флаг. Радиус: Штирлиц (для `kind=person` карточка целиком из памяти модели, деградация тихая — промпт велит писать «свежих публикаций не нашёл»), `b24_lead_from_xlsx.py`, `retail_lead_from_xlsx.py`.
- 🔴 **Zabbix severity сравнивается регистрозависимо** — `ESCALATING_SEVERITIES = {"Warning", "Average", "High", "Disaster"}` матчится через `severity not in ...` **без нормализации регистра** (`zabbix_monitor.py:19,101`), скип уходит в `logger.debug`. Если шаблон отдаёт «warning» строчными — проблема не эскалируется вообще и молча. **Фактический регистр от нашего шаблона в репозитории не зафиксирован** (фикстур нет, таблица `zabbix_problems` локально пуста) — проверять на проде: `select distinct severity from zabbix_problems`. Дешёвая страховка независимо от ответа — `.casefold()` с обеих сторон. Плюс `Severity:\s*(\S+)` берёт только первое слово: «Not classified» → «Not» (это уже баг безусловно).
- 🔴 **UnboundLocalError в `socrates.py:191`** — `duration_min` присваивается только под успешным ffprobe, читается безусловно. Оба probe упали → UnboundLocalError мимо `_StageAbort` → wait_msg навсегда застывает на «Конвертирую аудио…». Следствие: оценка длительности по размеру опуса — практически мёртвый код; отсечка `MEETING_MAX_MINUTES` работает только при успешном probe.
- 🟡 **Звонки (voximplant) — единственная метрика без пагинации**: `len(calls_raw)` по 50 строкам. Каскад на `calls_count`, `calls_total_seconds`, `calls_by_direction` и выборку транскрипций (25 длиннейших из первых 50, а не из всех). Коммит e4b8b9e («фикс всегда 50») блок voximplant не тронул.
- 🟡 **Хадсон: двойной вычет нормы.** `_count_absence_workdays` считает все Пн-Пт отпуска не исключая праздники, а `build_reports` вычитает праздники и отлучки последовательно из одной базы. Праздничный Пн внутри отпуска Пн-Ср: 32−8−24 = 0 → ложный `on_leave=True`, хотя человек работал Чт-Пт (корректно 32−8−16=8).
- 🟡 **`get_absences` фильтрует вложенность, а не пересечение** (`>=DATE_ACTIVE_FROM: since` + `<=DATE_ACTIVE_TO: until`), тогда как downstream клампит под overlap → переходящий отпуск теряется. Маскируется тем, что пустой ответ проваливается в календарный fallback. Фикс: `<=DATE_ACTIVE_FROM: until`, `>=DATE_ACTIVE_TO: since`.
- 🟡 **`deals_won` за период и месячный факт считаются по-разному**: период — `STAGE_ID.endswith("WON") and CLOSED=="Y"` по окну `DATE_MODIFY`; месяц — `STAGE_SEMANTIC_ID="S"` по `CLOSEDATE`. В одном сообщении рядом стоят «🏆 Закрыто WON за период» и «Факт», посчитанные по разным правилам. Кастомная успешная стадия без суффикса `WON` попадёт в факт и не попадёт в `deals_won`.
- 🟡 **`_attendee_picker` callback_data**: `pick:{id}:{name[:40]}` — срез по символам, лимит Telegram 64 **байта**, кириллица 2 байта/символ → 40 символов = 80 байт → `BUTTON_DATA_INVALID`, вся клавиатура не отрисуется.
- 🟡 **Rejection-классификатор идёт на Sonnet, не на Haiku** — `classify_rejection_intent` зовёт `ai_client.complete(prompt, timeout=30)` **без `model=`**. Вызывается на каждое входящее сообщение кандидата.
- 🟡 **Сократ: провал `split_audio` тихо откатывается** на один запрос ко всей записи (плюс ветка `if not chunks` вообще без лога) — ровно тот `provider_overloaded`, ради обхода которого v4.39.0 и делался. Пользователю уже ушёл тик «режу на N частей».
- 🟡 **Лейблы спикеров не ремапятся между чанками**: каждый чанк — отдельное аудио для Gemini, «один голос — один номер» гарантируется только внутри куска. Реплики после 20-й минуты могут приписываться не тому человеку; «Обнаружено спикеров: N» = максимум по одному куску (4 участника по двое в чанке → покажет 2).
- 🟡 **Отправка вопросов кандидату молча двигает стадию назад** в «Скриннинг резюме» (без `allowed_source_stages`).
- 🟡 **«📊 Суммаризация» в личке**: проверка членства обёрнута в try/except с `pass` → при ошибке API группа **ВКЛЮЧАЕТСЯ** в обзор → юзер может увидеть саммари чата, где его нет.
- 🟡 **`lead.py:119` пишет в Bitrix COMMENTS сырой AI-вывод без `_strip_emoji`** — тот самый utf8mb3-случай. Стрипалка есть только в recon-скриптах (`b24_lead_from_xlsx.py`, `retail_lead_from_xlsx.py`), в роутерах её нет.
- 🟢 **Васькова получает два комплекта .md** (8-10 файлов вместо 4-5) — нет дедупа между циклом менеджеров и веткой РОПа.
- 🟢 **`resolve_phone` не чистит контакты** — `DeleteContactsRequest` импортирован и не используется; контакт-лист личного аккаунта рекрутёра растёт бесконечно.
- 🟢 **`/api/bitrix/notify` возвращает 200** с `{ok: false, error}` при ненайденном юзере и ошибке отправки — бизнес-процесс Б24 по HTTP-коду ошибку не увидит.
- 🟢 **Бот пропустил 🟢 Zabbix (рестарт)** → проблема висит `resolved_at IS NULL` вечно → через 24ч Jira по уже закрытой. Сверки с Zabbix API нет.

### Ограничения и грабли

- Claude CLI: legacy refresh tokens — single-use, потеря = re-auth. Провал рефреша **не бросает исключение** — логируется error, остаётся старый токен; симптом вылезет позже как 401. `_refresh_lock` защищает только внутри процесса — два контейнера → гонка.
- **Claude CLI читает env `CLAUDE_CODE_OAUTH_TOKEN` ПЕРВЕЕ `~/.claude/credentials.json`**. В legacy-режиме `_sync_cli_credentials()` после рефреша пишет credentials.json, чтобы прямой `docker exec bot claude --print …` не ловил 401. **В long-lived режиме `_save()` не вызывается вовсе** — credentials.json не синхронизируется, и `data/.claude_token.json` бот не трогает (чинить 401 надо в .env, а не в файле).
- Email guests cannot be created via Bitrix REST API (UI only)
- Bitrix OAuth — shared file-based, не per-user
- Email guest cache — in-memory, throttle 0.3s между батчами, leave-unloaded on full failure
- OpenRouter image gen — может silently refuse по content policy (0 completion tokens = refusal)
- Potok scored: convention `^\d{3}-` префикс к фамилии — fragile, при ручном переименовании ломается
- Potok API SSL: русские CA — работает с Ubuntu, может падать с Mac без российского CA bundle
- Tailscale + OpenVPN: redirect-gateway конфликт — без server-side split tunnel не запустить вместе
- Socrates SSRF guard — узкое DNS-rebinding окно (TOCTOU). Полностью закрывается pinned-IP transport, не делали. Компенсация: внутренний Tailscale-only deployment + Bitrix-bound `/start` auth.
- **Potok admin metadata в job description**: `Владельцы:` / `Ссылка для встречи:` парсятся регекспами в `recruiter.py`. Меняешь формат — лезь править regex.
- **Potok `Контакт с рекрутером` название** — у клиента может называться иначе. Везде где сравниваем по имени — case-insensitive.
- **Potok `/client_api/*` (HH messaging)** — frontend tokens не ротируются, TTL нигде не проверяется (докстринг говорит «~6 недель», прежняя дока — «~5 месяцев»; обе цифры — догадки, обработки 401 нет). При 401 — переэкстрагировать из браузера. Документации нет, ловили через DevTools.
- **HH cold messaging deprecated** — отправка работает только при активном negotiation у кандидата.
- **DaData финансы — NULL** на бесплатном тарифе. Берём финансы из ГИР БО ФНС.
- **VOK API Saby** — платная подписка, не интегрируем.
- **`crm.stagehistory.list` нельзя фильтровать по user** — только по `OWNER_ID=deal_id`. N+1 по сделкам менеджера.
- **`tasks_done` метрика убрана** — в DC не работают по «задачам», метрика бесполезна.
- **State-файлы должны жить в `data/`** — volume-mounted (`./data:/app/data`). Любые `Path("xxx.json")` в корне `/app/` УБИВАЮТСЯ при `docker compose up --build`. Прецедент: `b24_processed.json` лежал в корне → 1000 лидов в B24 при 108 в state.
- **Bitrix CRM `COMMENTS` — MySQL utf8 (3-байтовая)** — 4-байтные emoji (📊 💡 🔧, supplementary plane U+10000+) обрезают всё поле начиная с первой эмодзи. Чистим `_strip_emoji()`. Русские буквы и `₽` (BMP) — норм.
- **Bitrix CRM `COMMENTS` — BBCODE, не HTML** — `<b>...</b>` ломает field. Используем `[B][/B]`, `[BR]`. В `timeline.comment` — обычные `\n` норм (retail-скрипт это правило нарушает).
- **WebFetch от Claude CLI ходит с Anthropic dataclass IP** — многие RU-сайты (Cloudflare, гео-блок, антибот) висят бесконечно, stderr пустой. Решение: 180с timeout → fallback на WebSearch-only промпт, `site_unreachable=true`, лид всё равно создаётся.
- **Bitrix `absence.list` 404** — требует HR-модуль (платный). Fallback через `calendar.event.get` + ключевые слова. `_ABSENCE_LIST_DEAD` (глобал модуля, живёт до рестарта) кэширует 404, ловит **только** подстроку «method not found». `get_absences` возвращает даты в ДВУХ форматах (absence.list → ISO, fallback → `DD.MM.YYYY`), а downstream требует ISO → иначе **молча** пустые списки.
- **APScheduler НЕ догоняет пропущенные cron'ы после рестарта** — ребут после 11:00 Пн вайпает hudson_weekly до следующего понедельника. Руками `scripts/run_hudson_now.py` или `misfire_grace_time` (не сделали).
- **Industries для B24** — Claude каждый раз даёт уникальную формулировку (1409 уникальных из 1479 лидов). Keyword-классификация в 18 категорий (`INDUSTRY_GROUPS`), порядок важен — узкие (Стоматология, Риелторы, Промышленное оборудование) РАНЬШЕ широких (Медицина, Застройщики, B2B-сервис), первое совпадение выигрывает. Докстринги скрипта говорят «14 категорий» — устарели.
- **Локальная `data/arkadyjarvis.db` протухла** — 14 строк в `hudson_managers`, всё ещё Даниленко. Код (4/15) и БД расходятся до прогона `init_hudson_db.py`.
