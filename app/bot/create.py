from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings


def create_bot() -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    # Import and include routers (order matters — catch-all last)
    from app.bot.routers.start import router as start_router
    from app.bot.routers.summarize import router as summarize_router
    from app.bot.routers.meeting import router as meeting_router
    from app.bot.routers.free_slots import router as free_slots_router
    from app.bot.routers.jira_task import router as jira_task_router
    from app.bot.routers.lead import router as lead_router
    from app.bot.routers.image import router as image_router
    from app.bot.routers.ask_ai import router as ask_ai_router
    from app.bot.routers.contract import router as contract_router
    from app.bot.routers.employee import router as employee_router
    from app.bot.routers.glafira import router as glafira_router
    from app.bot.routers.recruiter import router as recruiter_router
    from app.bot.routers.group import router as group_router
    from app.bot.routers.buffer import router as buffer_router

    dp.include_routers(
        start_router,
        summarize_router,
        meeting_router,
        free_slots_router,
        jira_task_router,
        lead_router,
        image_router,
        ask_ai_router,
        contract_router,
        employee_router,
        glafira_router,
        recruiter_router,
        group_router,
        buffer_router,  # catch-all — must be last
    )

    return dp
