import asyncio
import json
import logging
import time
from pathlib import Path

from aiogram.exceptions import TelegramRetryAfter
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import db
from app.config import settings

# Telegram bot API allows ≈30 broadcast messages/sec. We throttle conservatively
# at 20/sec (50 ms between sends) — well under the limit and lets a 500-user
# broadcast finish in ~25 s without floodwaits.
_BROADCAST_INTERVAL = 0.05

logger = logging.getLogger("arkadyjarvis")
router = APIRouter()

TOKENS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "bitrix_tokens.json"


# ── Health ──────────────────────────────────────────────────────────────────


@router.get("/health")
async def health():
    checks: dict = {}

    # DB check
    try:
        _db = db.get_db()
        async with _db.execute("SELECT 1") as cur:
            await cur.fetchone()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    # Bitrix token expiry check
    try:
        if TOKENS_FILE.exists():
            tokens = json.loads(TOKENS_FILE.read_text())
            expires_at = tokens.get("expires_at", 0)
            remaining = expires_at - int(time.time())
            checks["bitrix_token"] = "ok" if remaining > 60 else f"expires in {remaining}s"
        else:
            checks["bitrix_token"] = "no token file"
    except Exception as e:
        checks["bitrix_token"] = f"error: {e}"

    ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if ok else "degraded", "checks": checks}


# ── Webhook: Bitrix24 → Telegram ────────────────────────────────────────────


class NotifyRequest(BaseModel):
    """Отправка уведомления пользователю в Telegram."""

    bitrix_user_id: int = Field(
        ..., description="ID пользователя в Bitrix24", examples=[42],
    )
    text: str = Field(
        ...,
        description="Текст сообщения (поддерживается HTML: <b>, <i>, <a>)",
        examples=["✅ Ваш отпуск с 01.04 по 14.04 утверждён"],
    )


class NotifyResponse(BaseModel):
    ok: bool
    telegram_id: int | None = Field(
        None, description="Telegram ID получателя (если найден)",
    )
    error: str | None = None


class BroadcastRequest(BaseModel):
    """Отправка сообщения всем активным пользователям."""

    text: str = Field(
        ...,
        description="Текст сообщения (поддерживается HTML: <b>, <i>, <a>)",
        examples=["📢 Завтра корпоратив в 18:00!"],
    )


class BroadcastResponse(BaseModel):
    ok: bool
    sent: int = Field(0, description="Количество успешно отправленных")
    failed: int = Field(0, description="Количество ошибок отправки")


def _check_token(token: str | None):
    """Validate webhook token."""
    if not settings.webhook_token:
        raise HTTPException(503, "WEBHOOK_TOKEN not configured on server")
    if token != settings.webhook_token:
        raise HTTPException(403, "Invalid token")


@router.post(
    "/bitrix/notify",
    response_model=NotifyResponse,
    summary="Отправить уведомление",
    description=(
        "Отправляет сообщение конкретному пользователю в Telegram по его Bitrix24 ID. "
        "Используйте в бизнес-процессах Б24 (активити «Вебхук»)."
    ),
    tags=["Bitrix24 Webhook"],
)
async def bitrix_notify(
    body: NotifyRequest,
    request: Request,
    x_webhook_token: str | None = Header(None, description="Токен авторизации"),
):
    _check_token(x_webhook_token)

    bot = request.app.state.bot

    user = await db.get_user_by_bitrix_id(body.bitrix_user_id)
    if not user:
        return NotifyResponse(ok=False, error=f"User bitrix_id={body.bitrix_user_id} not found")

    telegram_id = user["telegram_id"]
    try:
        await bot.send_message(telegram_id, body.text)
        logger.info(
            "Webhook notify: bitrix=%s → tg=%s, text=%r",
            body.bitrix_user_id, telegram_id, body.text[:80],
        )
        return NotifyResponse(ok=True, telegram_id=telegram_id)
    except Exception as e:
        logger.error("Webhook notify failed: %s", e)
        return NotifyResponse(ok=False, telegram_id=telegram_id, error=str(e))


@router.post(
    "/bitrix/broadcast",
    response_model=BroadcastResponse,
    summary="Рассылка всем",
    description=(
        "Отправляет сообщение всем активным пользователям бота в Telegram. "
        "Подходит для объявлений, напоминаний, корпоративных новостей."
    ),
    tags=["Bitrix24 Webhook"],
)
async def bitrix_broadcast(
    body: BroadcastRequest,
    request: Request,
    x_webhook_token: str | None = Header(None, description="Токен авторизации"),
):
    _check_token(x_webhook_token)

    bot = request.app.state.bot
    users = await db.get_active_users()

    sent = 0
    failed = 0
    for user in users:
        tg_id = user["telegram_id"]
        try:
            await bot.send_message(tg_id, body.text)
            sent += 1
        except TelegramRetryAfter as e:
            # Telegram asked us to back off — wait then retry once.
            logger.warning(
                "Broadcast rate-limited (tg=%s), sleeping %ss", tg_id, e.retry_after,
            )
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(tg_id, body.text)
                sent += 1
            except Exception as retry_err:
                logger.warning("Broadcast retry failed for tg=%s: %s", tg_id, retry_err)
                failed += 1
        except Exception as e:
            logger.warning("Broadcast failed for tg=%s: %s", tg_id, e)
            failed += 1
        await asyncio.sleep(_BROADCAST_INTERVAL)

    logger.info("Webhook broadcast: sent=%d, failed=%d", sent, failed)
    return BroadcastResponse(ok=True, sent=sent, failed=failed)
