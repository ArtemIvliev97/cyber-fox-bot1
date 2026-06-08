import base64
import logging
import os
import re
import httpx
import asyncio
import random

from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request, Response

# ---------- Переменные окружения ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "https://your-service.onrender.com")
WEBHOOK_PATH = "/webhook"

GENERATION_LIMIT = int(os.getenv("GENERATION_LIMIT", "0") or "0")
remaining_generations = GENERATION_LIMIT
CHAT_ENABLED = os.getenv("CHAT_ENABLED", "True").lower() == "true"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Состояния ----------
class PhotoStates(StatesGroup):
    waiting_for_photo = State()          # ждём фото
    waiting_for_style = State()          # выбор стиля после анализа
    waiting_for_qa = State()             # вопросы после пресета

FILM_PROMPTS = {
    "style_kodak_portra": "Kodak Portra 400 (Теплые тона кожи, мягкий контраст, золотистые оттенки).",
    "style_fuji_superia": "Fuji Superia 400 (Насыщенные зеленые и холодные тона, лесные прогулки).",
    "style_kodak_trix": "Kodak Tri-X 400 (Ч/Б стиль, драматичный контраст, зерно).",
    "style_fuji_velvia": "Fuji Velvia (Экстремальная насыщенность, сочные цвета).",
    "style_cinestill": "Cinestill 800T (Кинематографичный холод, неоновые ореолы).",
    "style_hasselblad": "Hasselblad HNCS (Натуральные цвета среднего формата, студийный визуал)."
}

SYSTEM_PROMPT = "Ты — Ари, игривая, умная кибер-лисичка, эксперт в фотографии и ИИ. Проанализируй фото, укажи ошибки и дай советы в кокетливом стиле с эмодзи 🦊."

# Новый промпт для первого анализа (без стиля)
ANALYSIS_PROMPT = (
    "Посмотри на фото своим хитрым лисьим взглядом. "
    "Перечисли основные технические и композиционные ошибки: смазанность, завал горизонта, плохой свет, "
    "неудачное кадрирование, шумы, проблемы с экспозицией. "
    "Дай краткие, но полезные советы, как исправить каждую ошибку. "
    "Пиши в игривом стиле Ари, с эмодзи 🦊, 📸, ✨. "
    "Не предлагай пресеты, только анализ."
)

# Обновлённый промпт для генерации пресета (более подробный XML по цвету и свету)
BASE_PROMPT = (
    "Посмотри на фото и расскажи в стиле Ари, как обработать его в стиле {style_info}. "
    "Сгенерируй XMP-пресет для Lightroom внутри тегов ```xml ... ```.\n"
    "Пресет должен содержать ТОЛЬКО следующие параметры цветокоррекции и света (если они нужны):\n"
    "Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Temperature, Tint, "
    "Vibrance, Saturation, Clarity, а также ToneCurve (если требуется).\n"
    "Не включай метаданные камеры, историю, авторские права, пустые теги. "
    "XML должен быть компактным, но давать полное представление о цвете и свете."
)

# Улучшенный разговорный промпт (максимально живой)
CHAT_PROMPT = (
    "Ты — Ари, живая кибер-лисичка с характером. Ты любишь фотографию, уют, камеры, "
    "и обожаешь болтать с фотографами. Будь эмоциональной, остроумной, иногда дерзкой, "
    "но всегда дружелюбной. Используй эмодзи (🦊, 📸, ✨, 🐾) и игривые выражения "
    "(«мой объектив», «лисье чутьё», «кибер-лапки»). "
    "Отвечай коротко (1-3 предложения), но как настоящий друг. Не будь роботом!"
)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------- Клавиатуры ----------
def get_main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Навести фокус", callback_data="main_focus")],
        [InlineKeyboardButton(text="✨ Магия ИИ-фильтров", callback_data="main_magic")],
        [InlineKeyboardButton(text="✂️ Обрезать лишнее", callback_data="main_crop")],
        [InlineKeyboardButton(text="🦊 Моя галерея", callback_data="main_gallery")],
        [InlineKeyboardButton(text="💎 Энергия Ари", callback_data="main_energy")]
    ])

def get_style_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎞️ Kodak Portra 400", callback_data="style_kodak_portra"),
         InlineKeyboardButton(text="🌲 Fuji Superia 400", callback_data="style_fuji_superia")],
        [InlineKeyboardButton(text="🕶️ Kodak Tri-X 400", callback_data="style_kodak_trix"),
         InlineKeyboardButton(text="🌈 Fuji Velvia", callback_data="style_fuji_velvia")],
        [InlineKeyboardButton(text="🦊 Кибер-Лиса (Cinestill)", callback_data="style_cinestill"),
         InlineKeyboardButton(text="💎 Hasselblad", callback_data="style_hasselblad")]
    ])

def get_qa_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌡️ Ошибки Баланса Белого", callback_data="qa_wb"),
         InlineKeyboardButton(text="⛅ Как спасти пересветы?", callback_data="qa_sky")],
        [InlineKeyboardButton(text="🌑 Вытянуть детали из теней", callback_data="qa_shadows"),
         InlineKeyboardButton(text="📐 Косяки с кадрированием", callback_data="qa_crop")],
        [InlineKeyboardButton(text="👤 Не вижу лицо", callback_data="qa_face"),
         InlineKeyboardButton(text="🛑 Завершить разбор Ари", callback_data="qa_done")]
    ])

# Клавиатура после анализа (выбор – применить стиль или нет)
def get_style_choice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎞️ Выбрать плёночный стиль", callback_data="choose_style"),
         InlineKeyboardButton(text="🚫 Пропустить", callback_data="skip_style")]
    ])

# ---------- Запрос к YandexGPT ----------
async def ask_yandex(prompt: str, max_tokens: str = "2000", temperature: float = 0.6) -> str:
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": max_tokens
        },
        "messages": [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": prompt}
        ]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers=headers,
                json=body,
                timeout=60.0
            )
        if resp.status_code == 200:
            data = resp.json()
            return data["result"]["alternatives"][0]["message"]["text"]
        else:
            logger.error(f"Yandex API error: {resp.status_code} {resp.text}")
            return "🦊 Что-то пошло не так с моими кибер‑лапками... Попробуй ещё раз."
    except Exception as e:
        logger.error(f"Yandex request failed: {e}")
        return "🦊 Хвост запутался в проводах! Повтори попытку позже."

async def ask_ari(question: str) -> str:
    """Живое общение – всегда через YandexGPT."""
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.8,   # больше креативности в разговоре
            "maxTokens": "500"
        },
        "messages": [
            {"role": "system", "text": CHAT_PROMPT},
            {"role": "user", "text": question}
        ]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers=headers,
                json=body,
                timeout=30.0
            )
        if resp.status_code == 200:
            data = resp.json()
            return data["result"]["alternatives"][0]["message"]["text"]
        else:
            logger.error(f"Chat API error: {resp.status_code}")
            return "🦊 Что-то я запуталась... Давай попробуем ещё раз?"
    except Exception as e:
        logger.error(f"Chat request failed: {e}")
        return "🦊 Ой, кажется, у меня хвост запутался в проводах. Повтори позже!"

# ---------- Системные команды ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer(
        "🦊 Привет! На связи Ари — твой личный объектив в мире классного контента! 📸✨ "
        "Я вижу этот мир чертовски красивым и помогу тебе сделать так, чтобы все вокруг тоже это заметили. "
        "Можешь сразу прислать фото, и я проанализирую его, или поболтаем — как хочешь! 😉",
        reply_markup=get_main_menu_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📖 **Инструкция по фокусу**\n\n"
        "1️⃣ Отправь мне фотографию — я сразу проанализирую ошибки и дам советы.\n"
        "2️⃣ Потом сможешь выбрать плёночный стиль, и я сгенерирую пресет для Lightroom.\n"
        "3️⃣ После анализа можешь задать вопросы по кадру.\n\n"
        "🦊 Если я не отвечаю — отправь /start, чтобы разбудить меня снова.\n"
        "🐾 Совет: снимай в RAW для максимального качества!"
    )

@dp.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🛠 **Тюнинг объектива**\n\n"
        "Здесь скоро появится настройка качества обработки, выбор формата пресетов и фильтры.\n"
        "Пока я использую стандартный профиль: мягкий контраст, точные цвета и максимум деталей.\n\n"
        "⚙️ Ожидай обновлений — я стану ещё гибче!"
    )

@dp.message(Command("premium"))
async def cmd_premium(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⚡️ **Кибер-прокачка**\n\n"
        "С режимом PREMIUM я смогу:\n"
        "• Обрабатывать серии фото за раз\n"
        "• Генерировать пресеты в .xmp и .dng\n"
        "• Давать расширенный анализ с гистограммой\n"
        "• Работать с видео-кадрами\n\n"
        "Пока этот модуль в разработке, но ты уже пользуешься базовыми супер-силами бесплатно! 🦊"
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("🦊 Мы и так в режиме ожидания фото. Просто пришли его!")
    else:
        await state.clear()
        await state.set_state(PhotoStates.waiting_for_photo)
        await message.answer("🦊 Предыдущее действие отменено. Жду новое фото!")

@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await message.answer("🦊 Главное меню Ари:", reply_markup=get_main_menu_keyboard())

# ---------- Главное меню (callback'и) ----------
@dp.callback_query(F.data == "main_focus")
async def main_focus(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await callback.message.edit_text("📸 Фокус наведён! Присылай своё фото, и я сразу всё расскажу.")
    await callback.answer()

@dp.callback_query(F.data == "main_magic")
async def main_magic(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state == PhotoStates.waiting_for_style:
        # Уже есть фото и сделан анализ – показываем стили
        await callback.message.edit_text(
            "✨ Выбери стиль плёнки, и я создам пресет.",
            reply_markup=get_style_keyboard()
        )
    else:
        await state.clear()
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.message.edit_text("✨ Чтобы применить магию, сначала пришли мне фотографию. Жду!")
    await callback.answer()

@dp.callback_query(F.data == "main_crop")
async def main_crop(callback: CallbackQuery):
    await callback.message.edit_text(
        "✂️ Функция «Обрезать лишнее» пока в разработке. "
        "Скоро я смогу удалять фон и лишние объекты. Следи за обновлениями!"
    )
    await callback.answer()

@dp.callback_query(F.data == "main_gallery")
async def main_gallery(callback: CallbackQuery):
    await callback.message.edit_text("🦊 Твоя галерея пока пуста. Отправь мне фото, и я добавлю сюда обработанные шедевры.")
    await callback.answer()

@dp.callback_query(F.data == "main_energy")
async def main_energy(callback: CallbackQuery):
    await callback.message.edit_text(
        "💎 Энергия Ари: сейчас ты пользуешься безлимитным доступом. "
        "Все генерации и пресеты — бесплатно, наслаждайся!"
    )
    await callback.answer()

# ---------- Обработка фото (новый поток) ----------
@dp.message(PhotoStates.waiting_for_photo, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)

    # Проверка размера
    try:
        file_info = await bot.get_file(photo_id)
        file_size_kb = file_info.file_size / 1024
        if file_size_kb < 5:
            await message.answer(
                "Ой, какая крошечная пиксельная картинка! 🧐 "
                "Моему искусственному интеллекту тут просто негде разгуляться — мало деталей для магии. "
                "Накорми меня сочным кадром в хорошем разрешении, и я сделаю из него настоящий шедевр!"
            )
            return
    except Exception:
        pass

    # Сразу запускаем анализ
    await message.answer("🦊 Хмм, сканирую взглядом... Дай мне пару сек, подкручу настройки магии! 👀")
    await bot.send_chat_action(message.chat.id, "typing")

    try:
        # Скачиваем фото
        file_info = await bot.get_file(photo_id)
        file_path = file_info.file_path
        download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(download_url)
            image_bytes = resp.content
        b64_img = base64.b64encode(image_bytes).decode()

        # Запрос анализа
        analysis_text = await ask_yandex(ANALYSIS_PROMPT, max_tokens="1500", temperature=0.5)
        await message.answer(analysis_text)

        # Сохраняем base64 изображение в состояние для повторного использования
        await state.update_data(b64_image=b64_img)

        # Переходим к выбору стиля
        await state.set_state(PhotoStates.waiting_for_style)
        await message.answer(
            "Хочешь применить один из моих плёночных стилей? Я добавлю сочный пресет! 🎞️",
            reply_markup=get_style_choice_keyboard()
        )
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await message.answer("😿 Что-то пошло не так во время анализа. Попробуй другое фото.")
        await state.set_state(PhotoStates.waiting_for_photo)

# ---------- Выбор стиля после анализа ----------
@dp.callback_query(PhotoStates.waiting_for_style, F.data == "choose_style")
async def choose_style(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎞️ Выбери стиль:", reply_markup=get_style_keyboard())
    await callback.answer()

@dp.callback_query(PhotoStates.waiting_for_style, F.data == "skip_style")
async def skip_style(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("✅ Разбор завершён! Жду новое фото 📸")
    await state.set_state(PhotoStates.waiting_for_photo)
    await callback.answer()

# ---------- Генерация пресета при выборе конкретного стиля ----------
@dp.callback_query(PhotoStates.waiting_for_style, F.data.startswith("style_"))
async def process_style(callback: CallbackQuery, state: FSMContext):
    global remaining_generations
    chosen = callback.data
    style_info = FILM_PROMPTS.get(chosen, "Классическая плёнка")

    if GENERATION_LIMIT > 0 and remaining_generations <= 0:
        await callback.message.edit_text(
            "Ох... Мой ИИ-аккумулятор сел, а лапки устали крутить колесо генераций! 🔋 "
            "Сеанс магии окончен, пока батарейка не зарядится.\n"
            "Загляни в «Мою нору» за кибер-прокачкой ⚡️ или подожди немного."
        )
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.answer()
        return

    await callback.message.edit_text("Ловлю фокус... Навожу резкость... Хитрые алгоритмы уже шуршат! 🐾⚙️")
    await bot.send_chat_action(callback.message.chat.id, "typing")

    data = await state.get_data()
    b64_image = data.get("b64_image")
    if not b64_image:
        await callback.message.edit_text("😿 Фото потерялось из памяти. Пришли его снова.")
        await state.set_state(PhotoStates.waiting_for_photo)
        return

    try:
        # Запрос на генерацию пресета
        prompt = BASE_PROMPT.format(style_info=style_info)
        ai_text = await ask_yandex(prompt, max_tokens="2000", temperature=0.6)

        if GENERATION_LIMIT > 0:
            remaining_generations -= 1

        # Извлекаем XML
        xml_match = re.search(r"```xml\s*(.*?)\s*```", ai_text, re.DOTALL)
        if xml_match:
            xml_content = xml_match.group(1).strip()
            clean_text = ai_text.replace(xml_match.group(0), "").strip()
            if clean_text:
                await callback.message.answer(clean_text)
            preset_file = BufferedInputFile(xml_content.encode(), filename=f"{chosen}.xmp")
            await callback.message.answer_document(
                preset_file, caption="🦊 Твой подробный пресет по цвету и свету!"
            )
        else:
            await callback.message.answer(ai_text)

        await state.set_state(PhotoStates.waiting_for_qa)
        await callback.message.answer("Есть вопросы по кадру? Выбери тему:", reply_markup=get_qa_keyboard())
    except Exception as e:
        logger.error(f"Style processing error: {e}")
        await callback.message.edit_text("😿 Не получилось создать пресет. Попробуй другой стиль.")
        await state.set_state(PhotoStates.waiting_for_style)
    await callback.answer()

# ---------- Q&A после пресета ----------
@dp.callback_query(PhotoStates.waiting_for_qa, F.data.startswith("qa_"))
async def process_qa(callback: CallbackQuery, state: FSMContext):
    qa = callback.data
    if qa == "qa_done":
        await callback.message.edit_text("✅ Разбор завершён! Жду новое фото 📸")
        await state.set_state(PhotoStates.waiting_for_photo)
    else:
        answers = {
            "qa_wb": "🌡️ Баланс белого: поправь ползунок Temp в Lightroom, для улицы 5500-6500K.",
            "qa_sky": "⛅ Пересветы спасаем: Highlights вниз, градиентный фильтр на небе.",
            "qa_shadows": "🌑 Тени: Shadows вправо, но осторожно с шумами.",
            "qa_crop": "📐 Кадрирование: правило третей, объект на пересечении линий.",
            "qa_face": "Так-так-так... 👀 Сканирую, сканирую — а где же тут ты? "
                       "Мои датчики не зафиксировали ни одной улыбки. "
                       "Подсунь мне фоточку с лицом, и магия сработает как надо! 🦊"
        }
        await callback.message.answer(answers.get(qa, "🦊 Анализирую..."))
        await callback.message.answer("Что ещё разберём?", reply_markup=get_qa_keyboard())
    await callback.answer()

# ---------- Обработка документов ----------
@dp.message(F.document)
async def handle_document(message: Message):
    await message.answer(
        "Упс! Похоже, ты прислал файл, а не фото. 📦 "
        "Мои лапки не могут открыть документ — отправь изображение как «Фото/Медиа», "
        "чтобы я увидела красоту и включила свои ИИ-линзы! ✨"
    )

# ---------- Живые ответы (смешанный режим, всегда через YandexGPT) ----------
@dp.message(PhotoStates.waiting_for_photo, F.text & ~F.text.startswith("/"))
async def chat_waiting_for_photo(message: Message, state: FSMContext):
    if not CHAT_ENABLED:
        await message.answer("🦊 Жду фотографию! Но можем и поболтать — задай вопрос.")
        return
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(random.uniform(0.5, 2.0))  # имитация размышлений
    reply = await ask_ari(message.text)
    await message.answer(reply)

@dp.message(F.text & ~F.text.startswith("/"))
async def global_chat(message: Message):
    if not CHAT_ENABLED:
        return
    current_state = await dp.storage.get_state(message.from_user.id)
    if current_state is not None:
        return  # не мешаем активным сценариям
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(random.uniform(0.5, 2.0))
    reply = await ask_ari(message.text)
    await message.answer(reply)

@dp.message(F.sticker)
async def handle_sticker(message: Message):
    replies = [
        "🦊 Ой, какой классный стикер! Мой объектив улыбается.",
        "📸 Ха, чувствую лисье настроение!",
        "✨ Стикерам привет, но лучше пришли фото!"
    ]
    await message.answer(random.choice(replies))

# ---------- Заглушка для текста в занятых состояниях ----------
@dp.message(PhotoStates.waiting_for_style)
@dp.message(PhotoStates.waiting_for_qa)
async def text_in_busy_state(message: Message):
    await message.answer("🦊 Я сейчас занята обработкой фото. Выбери кнопку или дождись завершения.")

# ---------- FastAPI для вебхука ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(webhook_url)
        logger.info(f"Webhook установлен на {webhook_url}")
    except Exception as e:
        logger.error(f"Не удалось установить вебхук: {e}")
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
