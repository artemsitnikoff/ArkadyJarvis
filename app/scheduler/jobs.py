import logging
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app import db
from app.config import settings
from app.services.ai_client import AIClient
from app.services.openrouter_client import OpenRouterClient
from app.services.prompts import load_prompt
from app.summarizer import build_daily_overview, summarize_messages

logger = logging.getLogger("arkadyjarvis")

FROG_STYLES = [
    "кубизм в духе Пабло Пикассо",
    "постимпрессионизм в духе Ван Гога с густыми мазками",
    "японская манга / аниме",
    "американский супергеройский комикс Marvel",
    "научная фантастика и космос в духе Star Wars",
    "киберпанк с неоновыми огнями в духе Blade Runner",
    "акварельная иллюстрация",
    "пиксель-арт 8-bit как в NES",
    "vaporwave с розово-фиолетовой палитрой",
    "чёрно-белое нуарное кино",
    "советский мультфильм «Ну, погоди!»",
    "фэнтези-иллюстрация в стиле Зельды",
    "3D-рендер в стиле мультфильмов Pixar",
    "египетские иероглифы и фрески",
    "минимализм Баухауса",
    "витражное стекло готического собора",
    "ретро-постер 50-х годов",
    "уличное граффити",
    "русский лубок",
    "картина эпохи Возрождения в духе Леонардо да Винчи",
    "поп-арт в стиле Энди Уорхола",
    "японская гравюра укиё-э (Хокусай)",
    "сюрреализм Сальвадора Дали с тающими формами",
    "LEGO-конструктор",
    "стим-панк с медными шестернями и паром",
    "классический Disney 90-х",
    "низкополигональная 3D-графика low-poly",
    "ASCII-арт на зелёном терминале",
    "сериал Rick and Morty",
    "Советский плакат Родченко / конструктивизм",
    "абстрактный экспрессионизм в стиле Джексона Поллока",
    "стиль ghibli Хаяо Миядзаки",
    "футуристичный holographic glass-morphism",
    "импрессионизм Клода Моне, кувшинки и размытые блики",
    "фовизм Анри Матисса с плоскими яркими цветами",
    "неопластицизм Пита Мондриана — чёрные линии и красно-сине-жёлтые прямоугольники",
    "мультсериал «Симпсоны» — жёлтые персонажи и выраженные контуры",
    "готический stop-motion Тима Бёртона",
    "кубический пиксельный мир Minecraft",
    "ретро-футуризм атомной эры Fallout",
    "ар-нуво Альфонса Мухи с орнаментами и женскими фигурами в обрамлении цветов",
    "чёрно-белый нуар Sin City с одним акцентным красным цветом",
    "стенсил-граффити Бэнкси с социальным подтекстом",
    "кьяроскуро Караваджо — резкий контраст света и тени",
    "психоделический постер 1960-х в духе Питера Макса",
    "геометрия Memphis Group 1980-х — точки, зигзаги и кислотные цвета",
    "синтвейв-постер 1980-х с неоновой сеткой и закатом",
    "загрузочный экран-постер из GTA V",
    "симметричная пастельная композиция Уэса Андерсона",
    "мультфильм Adventure Time Пендлтона Уорда",
    "подводный ар-деко BioShock",
    "традиционная old-school татуировка с толстыми контурами и розами",
    "китайская живопись тушью суми-э на рисовой бумаге",
    "византийская мозаика с золотым фоном",
    "детский рисунок цветными карандашами на тетрадном листе",
    "линогравюра — чёрно-белые грубые штрихи",
    "французский фантастический комикс Moebius / Heavy Metal",
    "французский комикс «Астерикс» Удерзо",
    "бельгийский комикс «Тинтин» Эрже с ligne claire",
    "дзен-каллиграфия энсо, одним росчерком туши",
    "глитч-арт с цифровыми искажениями и RGB-сдвигом",
    "золотой сецессион Густава Климта",
    "барочный драматизм Рубенса с пышными формами",
    "мультфильм субботним утром 80-х в духе He-Man / Ninja Turtles",
    "мексиканский papel picado — вырезанные из бумаги узоры",
]


async def daily_summary_job(bot: Bot, ai_client: AIClient):
    """Summarize each group chat and send daily overview to all active users via DM."""
    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    groups = await db.get_all_group_chats()
    if not groups:
        logger.info("=== No group chats for daily summary")
        return

    # chat_id -> (title, summary)
    chat_summaries: dict[int, tuple[str, str]] = {}

    for group in groups:
        chat_id = group["chat_id"]
        chat_title = group.get("chat_title") or str(chat_id)
        try:
            msgs = await db.get_buffered_messages(chat_id, since=start_of_day)
            if not msgs:
                logger.info("=== No messages today in %s", chat_title)
                continue

            summary = await summarize_messages(msgs, ai_client=ai_client)
            chat_summaries[chat_id] = (chat_title, summary)
            logger.info("=== Summarized group: %s (%d messages)", chat_title, len(msgs))
        except Exception as e:
            logger.error("=== Error summarizing group %s: %s", chat_title, e, exc_info=True)

    # Build personalized overview per user (only groups they belong to)
    if chat_summaries:
        users = await db.get_active_users()
        for user in users:
            tg_id = user["telegram_id"]
            # Filter summaries to groups this user is a member of
            user_summaries: list[tuple[str, str]] = []
            for chat_id, (title, summary) in chat_summaries.items():
                try:
                    member = await bot.get_chat_member(chat_id, tg_id)
                    if member.status not in ("left", "kicked"):
                        user_summaries.append((title, summary))
                except Exception:
                    pass  # bot can't check membership — skip this group

            if not user_summaries:
                continue

            try:
                overview = await build_daily_overview(
                    user_summaries, ai_client=ai_client,
                    user_name=user.get("display_name", ""),
                )
                await bot.send_message(
                    tg_id,
                    f"#summary\n📊 <b>Обзор дня</b>\n\n{overview}",
                )
            except Exception as e:
                logger.warning(
                    "=== Could not send overview to user %s: %s", tg_id, e,
                )
        logger.info("=== Daily overview sent to users")

    # Cleanup old messages
    deleted = await db.cleanup_old_messages(days=7)
    if deleted:
        logger.info("=== Cleaned up %d old messages", deleted)


async def send_wednesday_frog(
    bot: Bot, ai_client: AIClient, openrouter: OpenRouterClient, chat_id: int,
):
    """Generate a fresh frog meme and send it to the given chat."""
    style = random.choice(FROG_STYLES)
    logger.info("=== Wednesday frog style: %s", style)

    meta_prompt = load_prompt("wednesday_frog").replace("{style}", style)
    image_prompt = (await ai_client.complete(meta_prompt)).strip()
    logger.info("=== Wednesday frog prompt: %s", image_prompt)

    image_bytes = await openrouter.generate_image(image_prompt)
    photo = BufferedInputFile(image_bytes, filename="wednesday_frog.png")
    caption = f"🐸 Со средой, мои чуваки!\n\n<i>стиль: {style}</i>"
    await bot.send_photo(chat_id, photo, caption=caption)
    logger.info("=== Wednesday frog sent to chat %s", chat_id)


async def wednesday_frog_job(bot: Bot, ai_client: AIClient, openrouter: OpenRouterClient):
    """Scheduler entry — reads chat_id from settings."""
    chat_id = settings.wednesday_frog_chat_id
    if not chat_id:
        logger.info("=== wednesday_frog_job skipped: WEDNESDAY_FROG_CHAT_ID not set")
        return
    try:
        await send_wednesday_frog(bot, ai_client, openrouter, chat_id)
    except Exception as e:
        logger.error("=== wednesday_frog_job failed: %s", e, exc_info=True)


async def send_monday_poster(
    bot: Bot, ai_client: AIClient, openrouter: OpenRouterClient, chat_id: int,
):
    """Generate a Soviet-1930s-style motivational Monday poster and send it to chat."""
    meta_prompt = load_prompt("monday_poster")
    image_prompt = (await ai_client.complete(meta_prompt)).strip()
    logger.info("=== Monday poster prompt: %s", image_prompt)

    image_bytes = await openrouter.generate_image(image_prompt)
    photo = BufferedInputFile(image_bytes, filename="monday_poster.png")
    await bot.send_photo(
        chat_id, photo, caption="🛠 Наконец-то понедельник — и на любимую работу!",
    )
    logger.info("=== Monday poster sent to chat %s", chat_id)


async def monday_poster_job(bot: Bot, ai_client: AIClient, openrouter: OpenRouterClient):
    """Scheduler entry — reads chat_id from settings."""
    chat_id = settings.monday_poster_chat_id
    if not chat_id:
        logger.info("=== monday_poster_job skipped: MONDAY_POSTER_CHAT_ID not set")
        return
    try:
        await send_monday_poster(bot, ai_client, openrouter, chat_id)
    except Exception as e:
        logger.error("=== monday_poster_job failed: %s", e, exc_info=True)


async def sales_dept_summary_job(bot: Bot, bitrix, ai_client: AIClient):
    """19:00: дневной отчёт по продажникам — отправляется в DM получателям."""
    import json as jsonmod

    from app.services.prompts import load_prompt
    from app.services.sales_analytics import collect_for_user_ids

    bitrix_ids_raw = settings.sales_report_bitrix_user_ids.strip()
    recipients_raw = settings.sales_report_recipients.strip()
    if not bitrix_ids_raw or not recipients_raw:
        logger.info(
            "=== sales_dept_summary_job skipped: SALES_REPORT_BITRIX_USER_IDS or _RECIPIENTS not set"
        )
        return

    try:
        bitrix_ids = [int(x.strip()) for x in bitrix_ids_raw.split(",") if x.strip()]
        recipients = [int(x.strip()) for x in recipients_raw.split(",") if x.strip()]
    except ValueError as e:
        logger.error("=== sales_dept_summary_job: bad IDs in config: %s", e)
        return

    try:
        activities = await collect_for_user_ids(bitrix, bitrix_ids, settings.timezone)
        data_json = jsonmod.dumps(
            [a.__dict__ for a in activities], ensure_ascii=False, indent=2, default=str,
        )
        prompt = load_prompt("sales_summary").replace("{data_json}", data_json)
        summary = await ai_client.complete(prompt, timeout=120)
    except Exception as e:
        logger.error("=== sales_dept_summary_job: collect/AI failed: %s", e, exc_info=True)
        return

    text = f"#анализ_отдела_продаж\n\n{summary}"
    for tg_id in recipients:
        try:
            await bot.send_message(tg_id, text)
        except Exception as e:
            logger.error("=== sales_dept_summary_job: send to %s failed: %s", tg_id, e)


async def zabbix_check_unresolved_job():
    """Daily 10:00: scan Zabbix problems open >24h, escalate to Jira (DA project)."""
    from app.services.jira_client import JiraClient
    from app.services.zabbix_monitor import check_unresolved_and_create_jira

    if not settings.zabbix_channel_id:
        logger.info("=== zabbix_check_unresolved_job skipped: ZABBIX_CHANNEL_ID not set")
        return
    try:
        async with JiraClient() as jira:
            count = await check_unresolved_and_create_jira(jira)
        logger.info("=== zabbix_check_unresolved_job: %d Jira tasks created", count)
    except Exception as e:
        logger.error("=== zabbix_check_unresolved_job failed: %s", e, exc_info=True)

