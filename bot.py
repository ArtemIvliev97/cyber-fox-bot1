import base64
import logging
import os
import re
import httpx

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

# Лимит генераций (0 = безлимит)
GENERATION_LIMIT = int(os.getenv("GENERATION_LIMIT", "0") or "0")
remaining_generations = GENERATION_LIMIT

# Включить живое общение? (True/False)
CHAT_ENABLED = os.getenv("CHAT_ENABLED", "True").lower() == "true"

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

CHAT_PROMPT = (
    "Ты — Ари, игривая кибер-лисичка, которая любит фотографию и уют. "
    "Отвечай коротко (1-3 предложения), тепло, с эмодзи (🦊, 📸, ✨). "
    "Пользователь просто хочет поболтать. Будь милой и остроумной."
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

# ---------- Функция запроса к YandexGPT для чата ----------
async def ask_ari(question: str) -> str:
    """Отправляет вопрос в YandexGPT и возвращает ответ Ари."""
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.7,
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
        "Ну что, поймаем идеальный кадр или сразу перейдем к магии? "
        "Выбирай кнопку ниже, не стесняйся! 😉",
        reply_markup=get_main_menu_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📖 **Инструкция по фокусу**\n\n"
        "1️⃣ Отправь мне фотографию — я проанализирую композицию, свет и цвета.\n"
        "2️⃣ Выбери стиль плёнки, и я подберу идеальный пресет для Lightroom.\n"
        "3️⃣ После анализа можешь задать вопросы по кадру — я подскажу, что поправить.\n\n"
        "🦊 Если я не отвечаю — просто отправь /start, чтобы пробудить меня снова.\n"
        "🐾 Совет: снимай в RAW, чтобы получить максимум от пресетов!"
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
        "Пока этот модуль в разработке, но ты уже можешь пользоваться базовыми супер-силами бесплатно! 🦊"
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
    await callback.message.edit_text("📸 Фокус наведён! Присылай своё фото, и я покажу, на что способна.")
    await callback.answer()

@dp.callback_query(F.data == "main_magic")
async def main_magic(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state == PhotoStates.waiting_for_style:
        await callback.message.edit_text(
            "✨ Магия ИИ-фильтров: выбери стиль плёнки, и я создам пресет.",
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

# ---------- Обработка фото ----------
@dp.message(PhotoStates.waiting_for_photo, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await state.set_state(PhotoStates.waiting_for_style)

    # Проверка размера файла
    try:
        file_info = await bot.get_file(photo_id)
        file_size_kb = file_info.file_size / 1024
        if file_size_kb < 5:
            await message.answer(
                "Ой, какая крошечная пиксельная картинка! 🧐 "
                "Моему искусственному интеллекту тут просто негде разгуляться — мало деталей для магии. "
                "Накорми меня сочным кадром в хорошем разрешении, и я сделаю из него настоящий шедевр!"
            )
            await state.set_state(PhotoStates.waiting_for_photo)
            return
    except Exception:
        pass

    await message.answer(
        "Хмм, сканирую взглядом... 👀 Дай мне пару сек, подкручу настройки магии!",
        reply_markup=get_style_keyboard()
    )

# ---------- Обработка документов (не фото) ----------
@dp.message(F.document)
async def handle_document(message: Message):
    await message.answer(
        "Упс! Похоже, ты случайно прислал мне файл, завернутый в документ. 📦 "
        "Мои лапки не могут развернуть эту обертку! Отправь мне это же изображение как «Фото/Медиа», "
        "чтобы я сразу увидела всю красоту и включила свои ИИ-линзы! ✨"
    )

# ---------- Обработка стиля ----------
@dp.callback_query(PhotoStates.waiting_for_style, F.data.startswith("style_"))
async def process_style(callback: CallbackQuery, state: FSMContext):
    global remaining_generations
    chosen = callback.data
    style_info = FILM_PROMPTS.get(chosen, "Классическая плёнка")

    # Проверка лимита генераций
    if GENERATION_LIMIT > 0 and remaining_generations <= 0:
        await callback.message.edit_text(
            "Ох... Мой ИИ-аккумулятор сел, а лапки устали крутить колесо генераций! 🔋 "
            "Сеанс магии окончен, пока батарейка не зарядится.\n"
            "Хочешь продолжить охоту за крутыми кадрами прямо сейчас? Загляни в «Мою нору» за кибер-прокачкой ⚡️ "
            "или подожди, пока энергия восстановится сама!"
        )
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.answer()
        return

    await callback.message.edit_text("Ловлю фокус... Навожу резкость... Хитрые алгоритмы уже шуршат! 🐾⚙️")
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

        if GENERATION_LIMIT > 0:
            remaining_generations -= 1

        result = ai_resp.json()
        ai_text = result["result"]["alternatives"][0]["message"]["text"]

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
        await callback.message.answer("Что разобрать дополнительно?", reply_markup=get_qa_keyboard())
    except httpx.TimeoutException:
        logger.error("Таймаут при обращении к API")
        await callback.message.edit_text(
            "Шок! Кажется, у меня в проводах застрял чей-то пушистый хвост... 🦊⚙️ "
            "Мои сервера устроили бунт и временно не отвечают. Я уже чиню свои микросхемы и навожу порядок. "
            "Дай мне пару минут и попробуй нажать кнопку еще раз!"
        )
        await state.set_state(PhotoStates.waiting_for_photo)
    except Exception as e:
        logger.error(f"Error: {e}")
        await callback.message.edit_text(f"😿 Ошибка: {e}")
        await state.set_state(PhotoStates.waiting_for_photo)
    await callback.answer()

# ---------- Q&A после обработки ----------
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
                       "Мои датчики не зафиксировали ни одной улыбки на этом кадре. "
                       "Подсунь мне фоточку, где хорошо видно твое личико (или лицо твоего друга), "
                       "и тогда лисья магия точно сработает как надо! 🦊"
        }
        await callback.message.answer(answers.get(qa, "🦊 Анализирую..."))
        await callback.message.answer("Что ещё разберём?", reply_markup=get_qa_keyboard())
    await callback.answer()

# ---------- Живые ответы Ари (смешанный режим) ----------
# 1. В состоянии ожидания фото
@dp.message(PhotoStates.waiting_for_photo, F.text & ~F.text.startswith("/"))
async def chat_waiting_for_photo(message: Message, state: FSMContext):
    if not CHAT_ENABLED:
        await message.answer("Ой, мой объектив такое не распознает! Пожалуйста, скорми мне красивую картинку, а не эти скучные буковки. 🦊 Наведи фокус!")
        return
    await bot.send_chat_action(message.chat.id, "typing")
    answer = await ask_ari(message.text)
    await message.answer(answer)

# 2. Вне состояний (глобальный чат)
@dp.message(F.text & ~F.text.startswith("/"))
async def global_chat(message: Message):
    if not CHAT_ENABLED:
        return  # просто игнорируем, если чат выключен
    # Проверяем, что не находимся в других состояниях
    current_state = await dp.storage.get_state(message.from_user.id)
    if current_state is not None:
        return  # не обрабатываем, чтобы не сломать сценарии (стиль, qa)
    await bot.send_chat_action(message.chat.id, "typing")
    answer = await ask_ari(message.text)
    await message.answer(answer)

# ---------- Заглушка для текста в других состояниях (стиль, qa) ----------
@dp.message(PhotoStates.waiting_for_style)
@dp.message(PhotoStates.waiting_for_qa)
async def text_in_busy_state(message: Message):
    await message.answer("Ой, мой объектив такое не распознает! Пожалуйста, скорми мне красивую картинку, а не эти скучные буковки. 🦊 Наведи фокус!")

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
