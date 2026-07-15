# ArkadyJarvis

Telegram-бот для команды Digital Clouds: AI-ассистенты, аналитика отдела продаж, аудит worklog'ов,
рекрутинг, разведка по контрагентам, интеграции с Bitrix24 / Jira / Potok.io.

Обычный бот от BotFather (не userbot). Telethon-userbot используется отдельно и только там, где Bot API
не справляется: чтение истории каналов и переписка с кандидатами с личного аккаунта рекрутёра.

> **Для разработки читай [CLAUDE.md](CLAUDE.md)** — там подробная архитектура, все ENV, известные баги и грабли.
> Этот файл — обзорный.

## Что умеет

Всё живёт за одной инлайн-клавиатурой (`MENU_KB` в `app/bot/routers/start.py`).

| Кнопка | Что делает |
|---|---|
| Сотрудник / Моя команда | Карточка сотрудника и своей команды из Bitrix24 |
| Встреча / Найди время | Событие в календаре Bitrix24; поиск свободных слотов на 5 рабочих дней + бронирование |
| Задача | Claude переформатирует описание под наш шаблон → задача в Jira |
| Лид | Лид в Bitrix CRM из текста или голосового (транскрипция через Gemini) |
| Мои встречи | Ближайшие события из календаря |
| Картинка | Генерация изображения (Gemini 3 Pro Image), в т.ч. по фото с подписью |
| Спроси AI | Claude с персонажем «Джарвис Аркадия» |
| Суммаризация | AI-саммари переписки в группах за сегодня |
| Проверь договор | PDF/DOCX/TXT → разбор по чек-листу |
| Цицерон | Юрист-консультант по RU-праву (ГК, КоАП, АПК, НК) |
| 🎓 Сократ | Запись встречи по ссылке → транскрипция с диаризацией → ревью + бриф |
| 🕵️ Штирлиц | Разведка по компании (DaData + ГИР БО ФНС) или человеку |
| 🤖 Марфа | AI офис-менеджер поверх OpenClaw (browser RPA) |
| 👔 Глафира | Рекрутёр: скоринг резюме из Potok.io, рассылка кандидатам, авто-отказ по ответу |
| 🏠 Мисис Хадсон | Еженедельный аудит worklog'ов отдела Production & Quality |

**Автоматически, по расписанию:**
- Ежедневный персонализированный обзор дня в личку (19:00, только по группам пользователя)
- Отчёт по отделу продаж: дневной (пн-пт 19:00) и недельный (пт 18:00) — метрики Bitrix + разбор записей звонков
- Аудит P&Q «Мисис Хадсон» (Пн 11:00): часы, простой, плохие worklog-комменты, задачи менеджерам в Jira
- Мониторинг Zabbix: проблема висит >24ч → задача в Jira (10:00)
- Мем-лягушка (Ср 10:00), конструктивистский плакат (Пн 9:00)

Плюс фоном: буферизация сообщений в группах для суммаризации.

## Стек

- **Python 3.11+**, aiogram v3, FastAPI, Uvicorn (uvicorn владеет event loop, polling — задача в lifespan)
- **Claude CLI** (подписка, `--print` subprocess) — основной путь для текстовых задач
- **OpenRouter** — Gemini 2.5 Pro (транскрипция), Gemini 3 Pro Image (генерация), плюс Haiku для массовых
  классификаторов мимо subscription-квоты
- **Telethon** — userbot (StringSession)
- **Bitrix24 REST**, **Jira REST**, **Potok.io** (REST + frontend `/client_api/*`), **OpenClaw**,
  **DaData**, **ГИР БО ФНС**
- **aiosqlite** — весь persistent state; **APScheduler** — все cron'ы; **pydantic-settings** — `.env`
- **ffmpeg** — конвертация и нарезка аудио для Сократа

## Быстрый старт

```bash
git clone https://github.com/artemsitnikoff/ArkadyJarvis.git
cd ArkadyJarvis
python3 -m venv .venv && source .venv/bin/activate
pip install .

# .env — см. раздел Config в CLAUDE.md.
# ⚠️ .env.example протух и НЕ копируется: `cp .env.example .env` уронит старт
#    с ValidationError (extra_forbidden). Собирай .env по CLAUDE.md.

uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Через Docker (порт 8002):
```bash
docker compose up --build
docker compose logs -f bot
```

Health-check: `curl localhost:8001/api/health` — отвечает **всегда 200**, статус смотри в теле ответа.

⚠️ **Правка `.env` требует `docker compose up -d --force-recreate`**, а не `restart`: docker `env_file`
перекрывает pydantic `.env`, и многие значения (access-листы, ID каналов) читаются на import модуля.

## Минимальная конфигурация

Полный список — в [CLAUDE.md → Config](CLAUDE.md#config-env). Формально обязателен только `BOT_TOKEN` —
единственное поле без дефолта, без него `Settings()` падает на импорте. Остальное ниже — де-факто
минимум для рабочего бота: процесс стартует и без этих переменных (health отдаёт 200, меню рисуется),
но AI и Bitrix упадут при первом же обращении.

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота (BotFather). **Единственный обязательный на старте** |
| `CLAUDE_CODE_OAUTH_TOKEN` | Токен подписки Claude. Long-lived — из `claude setup-token` (~1 год) |
| `BITRIX_CLIENT_ID` / `BITRIX_CLIENT_SECRET` / `BITRIX_DOMAIN` | Приложение Bitrix24 |
| `BITRIX_REFRESH_TOKEN` | Только для первого запуска, пока нет `data/bitrix_tokens.json` |

Остальное включает фичи по мере заполнения: `OPENROUTER_API_KEY` (аудио/картинки), `JIRA_*`, `POTOK_*`,
`TELETHON_*` (рекрутёр), `SALES_REPORT_*`, `HUDSON_*`, `ZABBIX_*`, `DADATA_API_KEY`, `OPENCLAW_*`.

> Наличие `CLAUDE_REFRESH_TOKEN` **переключает** режим токена на legacy auto-refresh. Для long-lived
> переменную надо убрать, а не добавить флаг.

## Авторизация пользователей

1. Пользователь пишет `/start` боту в личку
2. Бот ищет его `@username` в Bitrix (поле `BITRIX_TELEGRAM_FIELD`, default `UF_USR_1678964886664`)
3. Найден → связка `telegram_id ↔ bitrix_user_id` в SQLite

⚠️ AuthMiddleware по факту гейтит только `/summary`. Большинство AI-кнопок (Спроси AI, Картинка,
Цицерон, Сократ, Штирлиц, договор) авторизацию **не проверяют** — они доступны любому, кто нашёл бота.
Ограничены отдельными allow-list'ами по Telegram ID только Марфа, Глафира и Мисис Хадсон.

## Структура

Разбор по файлам — в [CLAUDE.md → Project Structure](CLAUDE.md#project-structure). Крупными мазками:

```
app/
  main.py        # lifespan: сборка сервисов, polling, ВСЕ CronTrigger'ы
  config.py      # pydantic-settings — все ENV
  db.py          # схема + миграции + CRUD
  bot/routers/   # по роутеру на фичу; buffer.py — catch-all, ВСЕГДА последний
  services/      # клиенты интеграций + доменная логика (sales, hudson, stirlitz, socrates)
  scheduler/     # тела cron-джобов
  api/           # health + вебхуки для Bitrix
prompts/         # все промпты .md — грузятся через load_prompt(name)
scripts/         # ручные прогоны, разовые конвейеры, разведка API
tests/           # pytest
```

## Тесты

```bash
pip install -e ".[dev]"
pytest
```

## Известные ограничения

Полный список (включая подтверждённые баги) — в [CLAUDE.md → Known Issues](CLAUDE.md#known-issues).
Самое важное:

- **WebSearch/WebFetch у Claude CLI не выполняются** — `--tools` даёт доступность, но не разрешение;
  нужен `--allowedTools`. Бьёт по Штирлицу и recon-конвейерам B24/retail: разведка тихо деградирует
  до знаний модели.
- **Zabbix severity сравнивается регистрозависимо**, без нормализации — если шаблон шлёт `warning`
  строчными, алерты не эскалируются вообще и молча. Фактический регистр в репозитории не зафиксирован,
  проверять на проде.
- **Email-гостей Bitrix нельзя создать через REST** (только через UI); в `user.get` они не находятся.
- **Bitrix OAuth-токены** — общий файл, не per-user.
- **Bitrix `absence.list`** требует платного HR-модуля → фолбэк на календарь по ключевым словам.
- **APScheduler не догоняет пропущенные cron'ы** после рестарта контейнера — гонять руками.
- **Claude Max weekly quota** — recon-конвейер на 4500 доменов сжирает её за пару дней.
