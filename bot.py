import base64
import logging
import os
import re
import httpx

from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request, Response

# ---------- Переменные окружения (задашь в Render) ----------
TOKEN = os.getenv("8638541424:AAEFx4yq1Fj35jEqp9JMtqDohRK5oCs1DrU")
YANDEX_API_KEY = os.getenv("AQVNxaJnocknSYhkyQalpieVUO6XQfkwfb3LEdfN")
YANDEX_FOLDER_ID = os.getenv("b1gsia4ac1iosglbb8hc")
# Render автоматически предоставляет этот URL
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "https://your-service.onrender.com")
WEBHOOK_PATH = "/webhook"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Состояния ----------
class PhotoStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_style = State()
    waiting_for_qa = State()

FILM_PROMPTS = {
    "style_kodak_portra": "Kodak Portra 400 (Теплые тона кожи, мягкий контраст, золотистые оттенки).",
    "style_fuji_superia": "Fuji Superia 400 (Насыщенные зеленые и холодные тона, лесные прогулки).",
    "style_kodak_trix": "Kodak Tri-X 400 (Ч/Б стиль, драматичный контраст, зерно).",
    "style_fuji_velvia": "Fuji Velvia (Экстремальная насыщенность, сочные цвета).",
    "style_cinestill": "Cinestill 800T (Кинематографичный холод, неоновые ореолы).",
    "style_hasselblad": "Hasselblad HNCS (Натуральные цвета среднего формата, студийный визуал)."
}

SYSTEM_PROMPT = "Ты — Ари, игривая, умная кибер-лисичка, эксперт в фотографии и ИИ. Проанализируй фото, укажи ошибки и дай советы в кокетливом стиле с эмодзи 🦊."
BASE_PROMPT = "Проанализируй фото с точки зрения колористики и экспозиции. Расскажи, как обработать под стиль {style_info}. Сгенерируй пресет Lightroom в ```xml ... ```."

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------- Клавиатуры ----------
def style_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎞️ Kodak Portra 400", callback_data="style_kodak_portra"),
         InlineKeyboardButton(text="🌲 Fuji Superia 400", callback_data="style_fuji_superia")],
        [InlineKeyboardButton(text="🕶️ Kodak Tri-X 400", callback_data="style_kodak_trix"),
         InlineKeyboardButton(text="🌈 Fuji Velvia", callback_data="style_fuji_velvia")],
        [InlineKeyboardButton(text="🦊 Кибер-Лиса (Cinestill)", callback_data="style_cinestill"),
         InlineKeyboardButton(text="💎 Hasselblad", callback_data="style_hasselblad")]
    ])

def qa_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌡️ Баланс белого", callback_data="qa_wb"),
         InlineKeyboardButton(text="⛅ Спасти пересветы", callback_data="qa_sky")],
        [InlineKeyboardButton(text="🌑 Тени", callback_data="qa_shadows"),
         InlineKeyboardButton(text="📐 Кадрирование", callback_data="qa_crop")],
        [InlineKeyboardButton(text="🛑 Завершить", callback_data="qa_done")]
    ])

# ---------- Обработчики (без изменений) ----------
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer("🦊 Привет! На связи **Ари** — твой кибер-объектив! 📸✨\nСкидывай фото, подберу пресет!")

@dp.message(PhotoStates.waiting_for_photo, F.photo)
async def got_photo(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await state.set_state(PhotoStates.waiting_for_style)
    await message.answer("Выбери плёночную магию:", reply_markup=style_kb())

@dp.callback_query(PhotoStates.waiting_for_style, F.data.startswith("style_"))
async def process_style(callback: CallbackQuery, state: FSMContext):
    chosen = callback.data
    style_info = FILM_PROMPTS.get(chosen, "Классическая плёнка")
    await callback.message.edit_text("🦊 Анализирую...")

    data = await state.get_data()
    photo_id = data.get("photo_id")
    if not photo_id:
        await callback.message.edit_text("😿 Фото потерялось. Пришли ещё раз.")
        await state.set_state(PhotoStates.waiting_for_photo)
        return

    try:
        file_info = await bot.get_file(photo_id)
        file_path = file_info.file_path
        download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

        async with httpx.AsyncClient() as client:
            resp = await client.get(download_url)
            image_bytes = resp.content
        b64_img = base64.b64encode(image_bytes).decode()

        # Запрос в YandexGPT
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
        body = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
            "completionOptions": {"stream": False, "temperature": 0.6, "maxTokens": "2000"},
            "messages": [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": BASE_PROMPT.format(style_info=style_info)}
            ]
        }
        async with httpx.AsyncClient() as client:
            ai_resp = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                                        headers=headers, json=body, timeout=60.0)
        if ai_resp.status_code != 200:
            await callback.message.edit_text(f"😿 Ошибка ИИ: {ai_resp.status_code}")
            await state.set_state(PhotoStates.waiting_for_photo)
            return

        result = ai_resp.json()
        ai_text = result["result"]["alternatives"][0]["message"]["text"]

        # Ищем XML пресет
        xml_match = re.search(r"```xml\s*(.*?)\s*```", ai_text, re.DOTALL)
        if xml_match:
            xml_content = xml_match.group(1).strip()
            clean_text = ai_text.replace(xml_match.group(0), "").strip()
            if clean_text:
                await callback.message.answer(clean_text)
            file = BufferedInputFile(xml_content.encode(), filename=f"{chosen}.xmp")
            await callback.message.answer_document(file, caption="🦊 Твой пресет для Lightroom!")
        else:
            await callback.message.answer(ai_text)

        await state.set_state(PhotoStates.waiting_for_qa)
        await callback.message.answer("Что разобрать дополнительно?", reply_markup=qa_kb())
    except Exception as e:
        logger.error(f"Error: {e}")
        await callback.message.edit_text(f"😿 Ошибка: {e}")
        await state.set_state(PhotoStates.waiting_for_photo)
    await callback.answer()

@dp.callback_query(PhotoStates.waiting_for_qa, F.data.startswith("qa_"))
async def qa_handler(callback: CallbackQuery, state: FSMContext):
    qa = callback.data
    if qa == "qa_done":
        await callback.message.edit_text("✅ Разбор завершён! Жду новое фото 📸")
        await state.set_state(PhotoStates.waiting_for_photo)
    else:
        answers = {
            "qa_wb": "🌡️ Баланс белого: поправь ползунок Temp в Lightroom, для улицы 5500-6500K.",
            "qa_sky": "⛅ Пересветы спасаем: Highlights вниз, градиентный фильтр на небе.",
            "qa_shadows": "🌑 Тени: Shadows вправо, но осторожно с шумами.",
            "qa_crop": "📐 Кадрирование: правило третей, объект на пересечении линий."
        }
        await callback.message.answer(answers.get(qa, "🦊 Анализирую..."))
        await callback.message.answer("Что ещё разберём?", reply_markup=qa_kb())
    await callback.answer()

@dp.message(PhotoStates.waiting_for_photo)
@dp.message(PhotoStates.waiting_for_style)
@dp.message(PhotoStates.waiting_for_qa)
async def fallback(message: Message):
    await message.answer("🦊 Жду фотографию, а не текст!")

# ---------- FastAPI для вебхука ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    logger.info(f"Webhook установлен на {webhook_url}")
    yield
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        update = await request.json()
        telegram_update = types.Update.model_validate(update, context={"bot": bot})
        await dp.feed_update(bot, telegram_update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return Response(status_code=200)
