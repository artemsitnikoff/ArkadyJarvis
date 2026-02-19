import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.api.routes import router as api_router
from app.bot.create import create_bot, create_dispatcher
from app.bot.middlewares import AuthMiddleware
from app.config import settings
from app.db import close_db, init_db

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

    # Register auth middleware on the dispatcher
    dp.message.outer_middleware(AuthMiddleware())

    # Start aiogram polling as a background task
    polling_task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False)
    )
    logger.info("Bot polling started")

    # Start scheduler
    from app.scheduler.jobs import daily_summary_job

    scheduler.add_job(
        daily_summary_job,
        CronTrigger(
            hour=settings.summary_hour,
            minute=settings.summary_minute,
            timezone=settings.timezone,
        ),
        id="daily_summary",
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
    from app.services.bitrix_client import BitrixClient
    await BitrixClient.get().close()
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(title="ArkadyJarvis", lifespan=lifespan)
app.include_router(api_router, prefix="/api")
