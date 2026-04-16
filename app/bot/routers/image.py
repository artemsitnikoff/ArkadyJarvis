import base64
import io
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message
from PIL import Image

from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvis")
router = Router()

MAX_IMAGE_SIZE = 1024  # max dimension in pixels


class ImageGen(StatesGroup):
    waiting_for_prompt = State()


async def _download_photo(message: Message, bot: Bot) -> str | None:
    """Download the largest photo, resize if needed, return base64 PNG string."""
    if not message.photo:
        return None
    file = await bot.download(message.photo[-1])
    img = Image.open(file)
    # Resize if too large
    if max(img.size) > MAX_IMAGE_SIZE:
        img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── FSM: waiting for prompt (from button) ─────────────────────

@router.message(ImageGen.waiting_for_prompt, F.photo)
async def handle_image_fsm_photo(message: Message, state: FSMContext, bot: Bot, openrouter):
    prompt = (message.caption or "").strip()
    if not prompt:
        await message.reply("Отправь фото с подписью — что сделать с картинкой.")
        return
    await state.clear()
    image_b64 = await _download_photo(message, bot)
    await _generate_and_send(message, prompt, openrouter=openrouter, image_b64=image_b64)


@router.message(ImageGen.waiting_for_prompt)
async def handle_image_fsm(message: Message, state: FSMContext, openrouter):
    prompt = (message.text or "").strip()
    if not prompt:
        await message.reply("Напиши промпт текстом или отправь фото с подписью.")
        return
    await state.clear()
    await _generate_and_send(message, prompt, openrouter=openrouter)


# ── Core ──────────────────────────────────────────────────────

async def _generate_and_send(
    message: Message, prompt: str, *, openrouter, image_b64: str | None = None,
):
    logger.info(
        "*** IMAGE: prompt=%r has_photo=%s from user=%s",
        prompt, image_b64 is not None, message.from_user.id,
    )
    wait_msg = await message.reply("🎨 Генерирую картинку...")
    try:
        image_bytes = await openrouter.generate_image(prompt, image_b64=image_b64)
        photo = BufferedInputFile(image_bytes, filename="image.png")
        await message.answer_photo(photo, reply_markup=MENU_KB)
        await wait_msg.delete()
    except Exception as e:
        logger.error("*** ERROR generating image: %s", e, exc_info=True)
        await wait_msg.edit_text(f"❌ Ошибка генерации: {e}")
