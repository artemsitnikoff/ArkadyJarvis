import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.api.routes import router as api_router
from app.bot.create import create_bot, create_dispatcher
from app.bot.middlewares import AuthMiddleware, ErrorMiddleware
from app.config import settings
from app.db import close_db, get_recruiter_contact, init_db
from app.scheduler.jobs import (
    daily_summary_job,
    monday_poster_job,
    wednesday_frog_job,
    zabbix_check_unresolved_job,
)
from app.services.ai_client import AIClient
from app.services.bitrix_client import BitrixClient
from app.services.openclaw_client import OpenClawClient
from app.services.openrouter_client import OpenRouterClient
from app.services.claude_token import init_token_file
from app.services.dadata_client import DaDataClient
from app.services.giro_client import GiroClient
from app.services.potok_client import PotokClient
from app.services.potok_frontend import PotokFrontendClient
from app.services.userbot import UserbotClient
from app.version import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arkadyjarvis")

scheduler = AsyncIOScheduler()
bot = create_bot()
dp = create_dispatcher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    logger.info("Database ready")

    init_token_file()

    # Create service instances and inject into dispatcher
    ai_client = AIClient()
    bitrix = BitrixClient()
    openrouter = OpenRouterClient()
    openclaw = OpenClawClient()
    potok = PotokClient()
    potok_frontend = PotokFrontendClient()
    dadata = DaDataClient()
    giro = GiroClient()

    userbot: UserbotClient | None = None
    if settings.telethon_session:
        try:
            userbot = UserbotClient()
            await userbot.start()

            async def _on_candidate_reply(sender_id: int, text: str) -> None:
                import html as html_mod
                from app.services.rejection_classifier import classify_rejection_intent

                contact = await get_recruiter_contact(sender_id)
                if not contact:
                    return

                logger.info(
                    "Userbot: reply from applicant %s (tg_id=%s): %r",
                    contact["applicant_name"], sender_id, text[:200],
                )
                await potok.post_candidate_reply(
                    contact["applicant_id"],
                    contact["job_id"],
                    contact["applicant_name"],
                    text,
                )

                # LLM-based rejection classification — log every result, act on score > threshold
                cls = await classify_rejection_intent(text, ai_client)
                score = cls["score"]
                reasoning = cls["reasoning"]
                threshold = settings.rejection_classifier_threshold
                logger.info(
                    "Rejection classifier %s: score=%d/%d — %s",
                    contact["applicant_name"], score, threshold, reasoning,
                )

                if score > threshold:
                    try:
                        await potok.set_applicant_active(
                            contact["applicant_id"], contact["job_id"], active=False,
                        )
                        # Audit trail in Potok timeline
                        await potok.post_comment(
                            contact["applicant_id"],
                            contact["job_id"],
                            (
                                f"<p><b>🤖 Авто-отказ</b> (порог {threshold}/100)</p>"
                                f"<p>Балл отказа: <b>{score}/100</b></p>"
                                f"<p>Причина: {html_mod.escape(reasoning)}</p>"
                            ),
                        )
                        logger.info(
                            "Auto-rejected %s (score=%d): %s",
                            contact["applicant_name"], score, reasoning,
                        )
                    except Exception as e:
                        logger.error(
                            "Auto-reject failed for applicant %s: %s",
                            contact["applicant_id"], e,
                        )

            userbot.set_reply_handler(_on_candidate_reply)
        except Exception as e:
            logger.warning("Userbot disabled: %s", e)
            userbot = None
    else:
        logger.info("Userbot: TELETHON_SESSION not set, userbot disabled")

    dp["ai_client"] = ai_client
    dp["bitrix"] = bitrix
    dp["openrouter"] = openrouter
    dp["openclaw"] = openclaw
    dp["potok"] = potok
    dp["potok_frontend"] = potok_frontend
    dp["dadata"] = dadata
    dp["giro"] = giro
    dp["userbot"] = userbot

    # Register middlewares on the dispatcher (order: error wraps auth wraps handler)
    # Applied to both messages and callback_query so callback handlers also get
    # error isolation and `db_user` injection.
    dp.message.outer_middleware(ErrorMiddleware())
    dp.callback_query.outer_middleware(ErrorMiddleware())
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())

    # Start aiogram polling as a background task
    polling_task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False)
    )
    logger.info("Bot polling started")

    # Start scheduler — pass bot and ai_client via args (no circular import)
    scheduler.add_job(
        daily_summary_job,
        CronTrigger(
            hour=settings.summary_hour,
            minute=settings.summary_minute,
            timezone=settings.timezone,
        ),
        id="daily_summary",
        args=[bot, ai_client],
    )
    if settings.wednesday_frog_chat_id:
        scheduler.add_job(
            wednesday_frog_job,
            CronTrigger(
                day_of_week="wed",
                hour=10,
                minute=0,
                timezone=settings.timezone,
            ),
            id="wednesday_frog",
            args=[bot, ai_client, openrouter],
        )
        logger.info(
            "Scheduler: wednesday_frog at Wed 10:00 [%s] -> chat %s",
            settings.timezone, settings.wednesday_frog_chat_id,
        )

    if settings.monday_poster_chat_id:
        scheduler.add_job(
            monday_poster_job,
            CronTrigger(
                day_of_week="mon",
                hour=9,
                minute=0,
                timezone=settings.timezone,
            ),
            id="monday_poster",
            args=[bot, ai_client, openrouter],
        )
        logger.info(
            "Scheduler: monday_poster at Mon 09:00 [%s] -> chat %s",
            settings.timezone, settings.monday_poster_chat_id,
        )

    if settings.zabbix_channel_id:
        scheduler.add_job(
            zabbix_check_unresolved_job,
            CronTrigger(hour=10, minute=0, timezone=settings.timezone),
            id="zabbix_check",
        )
        logger.info(
            "Scheduler: zabbix_check at 10:00 [%s] -> Jira project %s",
            settings.timezone, settings.zabbix_jira_project,
        )

    scheduler.start()
    logger.info(
        "Scheduler started: daily at %02d:%02d [%s]",
        settings.summary_hour,
        settings.summary_minute,
        settings.timezone,
    )

    yield

    # Shutdown
    scheduler.shutdown()
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    await bot.session.close()
    await ai_client.close()
    await bitrix.close()
    await openrouter.close()
    await openclaw.close()
    await potok.close()
    await potok_frontend.close()
    await dadata.close()
    await giro.close()
    if userbot:
        await userbot.stop()
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title="ArkadyJarvis",
    description="Telegram-бот для команды: Bitrix24, Jira, AI, рекрутинг",
    version=__version__,
    docs_url="/docs",
    lifespan=lifespan,
)
app.state.bot = bot
app.include_router(api_router, prefix="/api")
