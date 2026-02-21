# Деплой ArkadyJarvis

## Требования к серверу

- Docker + Docker Compose
- Git
- Доступ к OpenAI API (VPN или прокси, если регион заблокирован)

## Первоначальная установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/artemsitnikoff/ArkadyJarvis.git
cd ArkadyJarvis
```

### 2. Настроить .env

```bash
cp .env.example .env
nano .env
```

Заполнить все обязательные переменные: `BOT_TOKEN`, `OPENAI_API_KEY`, `BITRIX_CLIENT_ID`, `BITRIX_CLIENT_SECRET`, `OPENROUTER_API_KEY`.

При первом запуске также нужен `BITRIX_REFRESH_TOKEN`.

### 3. Перенести базу данных с Mac

На Mac:
```bash
# Скопировать базу и токены на сервер
scp data/arkadyjarvis.db user@server:~/ArkadyJarvis/data/
scp data/bitrix_tokens.json user@server:~/ArkadyJarvis/data/
```

На сервере убедиться, что папка `data/` существует:
```bash
mkdir -p data
```

### 4. Запустить

```bash
docker compose up -d --build
```

Проверить:
```bash
curl localhost:8002/api/health
docker compose logs -f
```

## Обновление (деплой новой версии)

```bash
cd ~/ArkadyJarvis
git pull
docker compose up -d --build
```

Одной строкой:
```bash
cd ~/ArkadyJarvis && git pull && docker compose up -d --build
```

### Проверка после обновления

```bash
# Health check
curl localhost:8002/api/health

# Логи
docker compose logs --tail=50

# Статус контейнера
docker compose ps
```

## Полезные команды

```bash
# Логи в реальном времени
docker compose logs -f

# Перезапуск без пересборки
docker compose restart

# Остановить
docker compose down

# Пересобрать и запустить
docker compose up -d --build

# Зайти в контейнер
docker compose exec bot bash
```

## Бэкап базы данных

```bash
# Скопировать с сервера
scp user@server:~/ArkadyJarvis/data/arkadyjarvis.db ./backup_$(date +%Y%m%d).db
```

## Структура data/

```
data/
  arkadyjarvis.db       # SQLite база (пользователи, креды, буфер сообщений)
  bitrix_tokens.json    # OAuth-токены Bitrix24 (автообновляются)
```

Оба файла персистентны через Docker volume (`./data:/app/data`). При `docker compose down` данные сохраняются.
