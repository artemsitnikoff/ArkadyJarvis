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
from app.db import close_db, init_db
from app.scheduler.jobs import daily_summary_job, monday_poster_job, wednesday_frog_job
from app.services.ai_client import AIClient
from app.services.bitrix_client import BitrixClient
from app.services.openclaw_client import OpenClawClient
from app.services.openrouter_client import OpenRouterClient
from app.services.claude_token import init_token_file
from app.services.potok_client import PotokClient
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

    dp["ai_client"] = ai_client
    dp["bitrix"] = bitrix
    dp["openrouter"] = openrouter
    dp["openclaw"] = openclaw
    dp["potok"] = potok

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
