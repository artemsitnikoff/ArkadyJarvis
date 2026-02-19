# ArkadyJarvis

Telegram-бот для команды: суммаризация чатов, создание встреч в Bitrix24, задачи в Jira, CRM-лиды.

## Возможности

| Команда | Что делает |
|---------|-----------|
| `суммаризация` | AI-саммари переписки за сегодня |
| `найди время @nick1 @nick2` | Свободные слоты на 5 рабочих дней + кнопки бронирования |
| `создай встречу 14:00 @nick1` | Событие в календаре Bitrix24 |
| `создай задачу DC Описание` | Задача в Jira |
| `создай лид Иванов, Рога и Копыта` | Лид в Bitrix CRM (AI извлекает данные) |
| `/start` | Авторизация (привязка Telegram к Bitrix) |
| `/jira` | Настройка Jira-аккаунта |

Дополнительно:
- Буферизация всех сообщений в группах для суммаризации
- Ежедневный автоматический отчёт по всем чатам (19:00)
- Автоответ на "ситников" цитатами Сенеки

## Стек

- **Python 3.11+**, aiogram v3, FastAPI, Uvicorn
- **OpenAI GPT-5.2** (суммаризация, извлечение данных из текста)
- **Bitrix24 REST API** (календарь, пользователи, CRM)
- **Jira REST API v2** (создание задач)
- **aiosqlite** (пользователи, креды, буфер сообщений)
- **APScheduler** (ежедневный отчёт)

## Быстрый старт

```bash
# 1. Клонировать и установить
git clone https://github.com/artemsitnikoff/ArkadyJarvis.git
cd ArkadyJarvis
python -m venv .venv && source .venv/bin/activate
pip install .

# 2. Настроить .env
cp .env.example .env  # заполнить BOT_TOKEN, OPENAI_API_KEY, BITRIX_*

# 3. Запустить
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Или через Docker:
```bash
docker compose up --build
```

## Конфигурация (.env)

| Переменная | Обязательная | Описание |
|-----------|:-----------:|---------|
| `BOT_TOKEN` | да | Токен Telegram-бота (BotFather) |
| `OPENAI_API_KEY` | да | API-ключ OpenAI |
| `BITRIX_CLIENT_ID` | да | ID приложения Bitrix24 |
| `BITRIX_CLIENT_SECRET` | да | Секрет приложения Bitrix24 |
| `BITRIX_REFRESH_TOKEN` | первый запуск | Для начальной авторизации |
| `BITRIX_DOMAIN` | нет | Домен Bitrix (для ссылок в ответах) |
| `OPENAI_MODEL` | нет | Модель OpenAI (по умолчанию `gpt-5.2`) |
| `DB_PATH` | нет | Путь к SQLite (по умолчанию `data/arkadyjarvis.db`) |
| `SUMMARY_HOUR` | нет | Час дневного отчёта (по умолчанию `19`) |
| `SUMMARY_MINUTE` | нет | Минута дневного отчёта (по умолчанию `0`) |
| `TIMEZONE` | нет | Часовой пояс (по умолчанию `Asia/Novosibirsk`) |

## Структура проекта

```
app/
  main.py                  # FastAPI + lifespan + aiogram polling + APScheduler
  config.py                # pydantic-settings из .env
  db.py                    # aiosqlite: схема, CRUD
  utils.py                 # Парсеры (время, участники, Bitrix datetime)
  summarizer.py            # GPT-суммаризация + очистка HTML
  bot/
    create.py              # Фабрики Bot + Dispatcher + регистрация роутеров
    middlewares.py          # AuthMiddleware (проверка авторизации)
    routers/
      start.py             # /start, /help, меню с подсказками
      auth.py              # /jira (FSM), /skip
      summarize.py         # суммаризация
      meeting.py           # создай встречу
      free_slots.py        # найди время + FSM бронирования
      jira_task.py         # создай задачу
      lead.py              # создай лид
      auto_reply.py        # ситников (Сенека)
      group.py             # бот добавлен/удалён из группы
      buffer.py            # catch-all буферизация сообщений
  services/
    ai_client.py           # AIClient singleton (OpenAI)
    bitrix_client.py       # BitrixClient singleton (OAuth, календарь, CRM)
    jira_client.py         # JiraClient (per-user, async context manager)
  scheduler/
    jobs.py                # Ежедневный автоотчёт
  api/
    routes.py              # GET /api/health
data/
  arkadyjarvis.db          # SQLite база
  bitrix_tokens.json       # OAuth-токены Bitrix (автообновление)
```

## Авторизация пользователей

1. Пользователь пишет `/start` боту в личку
2. Бот ищет `@username` в Bitrix (поле `UF_USR_1678964886664`)
3. Если найден — сохраняет связку `telegram_id ↔ bitrix_user_id` в SQLite
4. Опционально: `/jira` для настройки Jira-аккаунта

## Известные ограничения

- OpenAI API требует VPN (403 из-за региона)
- Email-гости Bitrix не находятся через `user.get` — используется кеш `im.user.list.get`
- Создать email-гостя через API нельзя, только через UI Bitrix
- Bitrix OAuth-токены хранятся в файле (shared для всех пользователей)
