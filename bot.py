import base64, logging, os, re, httpx, asyncio, random, io, zipfile, json
from collections import deque
from contextlib import asynccontextmanager
from io import BytesIO
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request, Response
import exifread
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

# ---------- Переменные окружения ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "https://your-service.onrender.com")
WEBHOOK_PATH = "/webhook"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GENERATION_LIMIT = int(os.getenv("GENERATION_LIMIT", "0") or "0")
remaining_generations = GENERATION_LIMIT
CHAT_ENABLED = os.getenv("CHAT_ENABLED", "True").lower() == "true"
VOICE_ENABLED = os.getenv("VOICE_ENABLED", "True").lower() == "true"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Инициализация бота и диспетчера ----------
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

ALL_USERS_FILE = "all_users.json"
all_users = set()
if os.path.exists(ALL_USERS_FILE):
    try:
        with open(ALL_USERS_FILE, "r") as f:
            all_users = set(json.load(f))
    except:
        pass

def save_all_users():
    with open(ALL_USERS_FILE, "w") as f:
        json.dump(list(all_users), f)

# ---------- Состояния ----------
class PhotoStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_style = State()
    waiting_for_qa = State()
    waiting_for_prompt = State()
    waiting_for_album_style = State()
    waiting_for_frame = State()
    waiting_for_sticker = State()
    waiting_for_voice_emotion = State()
    in_lesson = State()
    waiting_for_reminder = State()
    waiting_for_lut_description = State()
    # Заменены старые waiting_for_reference на два новых
    waiting_for_reference_source = State()
    waiting_for_reference_style = State()
    waiting_for_collage = State()
    # Новые состояния
    waiting_for_free_question = State()
    waiting_for_studio_photo = State()
    waiting_for_studio_effect = State()

# ---------- Хранилища ----------
user_memory = {}
user_personality = {}   # "wild" или "modest"
MEMORY_FILE = "user_memory.json"
if os.path.exists(MEMORY_FILE):
    try:
        data = json.load(open(MEMORY_FILE, "r"))
        if isinstance(data, dict):
            user_memory = data.get("memory", {})
            user_personality = data.get("personality", {})
    except:
        pass

def save_memory():
    with open(MEMORY_FILE, "w") as f:
        json.dump({"memory": user_memory, "personality": user_personality}, f)

user_context = {}
user_stats = {}
user_reminders = {}
user_last_photo = {}
ACHIEVEMENTS = {
    "first_photo": "📸 Первый кадр",
    "10_photos": "🔥 10 фотографий проанализировано",
    "all_styles": "🎨 Все стили опробованы",
    "voice_used": "🎤 Голос опробован",
    "album_used": "📚 Пакетная обработка",
    "lesson_done": "🎓 Урок пройден",
}
STATS_FILE = "user_stats.json"
if os.path.exists(STATS_FILE):
    try:
        with open(STATS_FILE, "r") as f:
            user_stats = json.load(f)
    except:
        pass

def save_stats():
    with open(STATS_FILE, "w") as f:
        json.dump(user_stats, f)

# Мини-уроки
LESSONS = [
    {
        "title": "Основы композиции",
        "steps": [
            "Правило третей: раздели кадр на 9 частей, объект на пересечении.",
            "Ведущие линии: используй дороги, реки, чтобы вести взгляд.",
            "Симметрия и паттерны создают гармонию.",
            "Негативное пространство подчёркивает объект.",
            "Отлично! Примени это в следующем кадре. 📸"
        ]
    }
]

# ---------- Локализация ----------
LOCALE = {
    "ru": {
        "start": "🦊 Ой, привет-привет! Это я, Ари — твой личный кибер-лисий объектив...",
        "help": "📖 <b>Инструкция по фокусу</b>...",
        "commands_list": "/start, /help, /commands, ...",
        "what_prompt": "Расскажи в двух-трёх игривых предложениях...",
        "news_prompt": "Придумай короткую, но увлекательную новость...",
        "podcast_prompt": "Расскажи короткий увлекательный подкаст...",
        "lut_prompt": "Сгенерируй LUT-файл...",
        "remind_set": "⏰ Напоминание установлено на {time}. Я скажу: «{text}»",
        "remind_trigger": "⏰ Напоминание! {text}",
        "post_instruction": "📲 Чтобы опубликовать фото...",
        "creator_answer": "🦊 Мой создатель — Артём!...",
        "frame_added": "🦊 Рамка плёнки добавлена!",
        "frame_prompt": "Пришли мне фото...",
        "sticker_done": "🦊 Вот твой будущий стикер!",
        "sticker_prompt": "Пришли фото...",
        "voice_emotion_set": "✅ Тембр голоса изменён на: {emotion}.",
        "voice_emotion_prompt": "Выбери настроение голоса Ари:",
        "stats_text": "📊 Твоя статистика...",
        "lesson_start": "🎓 Начинаем мини-урок: {title}.",
        "lesson_next": "Далее ➡️",
        "lesson_prev": "⬅️ Назад",
        "lesson_finish": "✅ Завершить",
        "admin_features": "🛠 Все функции бота Ари...",
        "settings": "🛠 Настройки временно недоступны.",
        "premium": "⚡️ Премиум-возможности пока в разработке.",
        "choose_style": "🎞️ Выбери стиль",
        "skip_style": "✅ Разбор завершён!",
        "all_styles": "📋 Все стили",
        "analysis_start": "🦊 Хмм, сканирую взглядом...",
        "small_photo": "Ой, какая крошечная...",
        "document_error": "Упс! Похоже, ты прислал файл...",
        "style_processing": "Ловлю фокус...",
        "preset_caption": "🦊 Твой пресет для Lightroom!",
        "album_preset_caption": "🦊 Твои пресеты для альбома.",
        "qa_choose": "Есть вопросы по кадру?",
        "qa_done": "✅ Разбор завершён!",
        "main_focus": "📸 Фокус наведён!",
        "main_magic": "✨ Магия ИИ-фильтров",
        "main_crop": "✂️ Функция «Обрезать лишнее» пока в разработке...",
        "main_gallery": "🦊 Твоя галерея пока пуста...",
        "main_energy": "💎 Энергия Ари: безлимит.",
        "main_generate": "🎨 Генератор изображений",
        "main_news": "📰 Новости",
        "main_commands": "📋 Команды",
        "lang_switched": "🦊 Язык изменён на русский 🇷🇺",
        "generate_prompt": "🎨 Опиши, что хочешь увидеть...",
        "generating": "🦊 Рисую...",
        "generated": "✨ Вот что получилось!",
        "generate_error": "😿 Не получилось сгенерировать...",
        "ask_for_photo": "🦊 Чтобы я проанализировала...",
        "busy_photo_override": "🦊 Вижу, ты прислал новое фото...",
        "new_analysis": "🔄 Новый анализ",
        "voice_unrecognized": "🦊 Не разобрала твой голос...",
        "voice_analysis_request": "🦊 Чтобы я проанализировала...",
        "where_are_you_reply": "🦊 Тут, тут! Хвостиком виляю...",
        "compliments": ["...", "..."],
        "album_detected": "🦊 Ого, целый альбом!",
        "album_choose_style": "🎞️ Выбери стиль для всех фото:",
        "news_generating": "🦊 Сейчас покопаюсь в ленте...",
        "mood_positive": ["Ты прям светишься!", ...],
        "mood_negative": ["Ой, кажется, тебе грустно...", ...],
        "mood_neutral": ["Слушаю тебя внимательно!", ...],
        "vision_prompt": "На фотографии я вижу: {objects}.",
        "reference_prompt": "Проанализируй два фото...",
        "collage_ready": "🦊 Коллаж готов!",
        "collage_prompt": "Пришли до 4 фото...",
        "idea_prompt": "Придумай креативную идею для фотосессии...",
        "voice_edit_done": "🦊 Применила правки!",
        "voice_edit_fail": "😿 Не поняла правки.",
        "lightroom_instruction": "🦊 Чтобы установить пресет...",
        "modest_on": "🦊 Хорошо, я приглушу искорки...",
        "wild_on": "🦊 Ура! Я снова в своей тарелке!...",
        "cancel": "❌ Действие отменено. Жду новую фотку 📸",
        "studio_prompt": "📸 Пришли селфи для виртуальной студии!",
        "studio_choose": "Выбери эффект:",
        "aristikers_done": "🦊 Твой стикерпак с Ари!",
        "preview_caption": "🦊 Примерный результат",
    }
}

# ---------- Стили плёнок и иконки ----------
FILM_PROMPTS = { ... }  # без изменений
STYLE_ICONS = { ... }

# ---------- Системные промпты ----------
SYSTEM_PROMPT = ( ... )
ANALYSIS_PROMPT = (
    "Посмотри на фото своим хитрым лисьим взглядом. "
    "Разбери его по пунктам и обязательно дай числовые рекомендации для Lightroom:\n"
    "- Экспозиция (Exposure): текущий уровень, на сколько его изменить.\n"
    "- Контраст (Contrast): насколько увеличить/уменьшить.\n"
    "- Света (Highlights) и тени (Shadows): на сколько сдвинуть ползунки.\n"
    "- Баланс белого (Temperature и Tint): оценочная температура, рекомендуемая.\n"
    "- Чёрные и белые точки (Blacks, Whites): нужна ли коррекция.\n"
    "- Насыщенность (Vibrance, Saturation): на сколько изменить.\n"
    "- Чёткость (Clarity): рекомендованное значение.\n"
    "- Шум и резкость: есть ли проблемы, что делать.\n"
    "- Композиция: ошибки кадрирования, как поправить.\n"
    "- Освещение: определи тип (мягкое, жёсткое, контровое, закатное и т.д.) и дай советы по его улучшению.\n"
    "Для каждого пункта пиши конкретную цифру. Не предлагай пресеты.\n"
    "После всех рекомендаций добавь JSON с параметрами для автокоррекции строго в формате:\n"
    '```json\n{"exposure": 0.0, "contrast": 0, "highlights": 0, "shadows": 0, "temperature": 0, "vibrance": 0, "clarity": 0}\n```\n'
    "Пиши с юмором и лёгкой иронией."
)
BASE_PROMPT = ( ... )
CHAT_PROMPT_WILD = ( ... )
CHAT_PROMPT_MODEST = ( ... )

def get_chat_prompt(user_id: str) -> str:
    return CHAT_PROMPT_WILD if user_personality.get(user_id, "wild") == "wild" else CHAT_PROMPT_MODEST

# ---------- Клавиатуры ----------
# ... все существующие клавиатуры без изменений, кроме get_qa_keyboard, добавим кнопки "✨ Применить магию" и "💬 Свой вопрос"
def get_qa_keyboard(lang="ru"):
    loc = LOCALE.get(lang, LOCALE["ru"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌡️ Баланс белого", callback_data="qa_wb"),
         InlineKeyboardButton(text="⛅ Пересветы", callback_data="qa_sky")],
        [InlineKeyboardButton(text="🌑 Тени", callback_data="qa_shadows"),
         InlineKeyboardButton(text="📐 Кадрирование", callback_data="qa_crop")],
        [InlineKeyboardButton(text="👤 Не вижу лицо", callback_data="qa_face"),
         InlineKeyboardButton(text="🛑 Завершить", callback_data="qa_done")],
        [InlineKeyboardButton(text="✨ Применить магию", callback_data="qa_auto_correct"),
         InlineKeyboardButton(text=loc["new_analysis"], callback_data="new_analysis")],
        [InlineKeyboardButton(text="💬 Свой вопрос", callback_data="qa_free_question")]
    ])

# ---------- Yandex Vision ----------
async def analyze_objects(image_b64: str) -> str: ...

# ---------- Коллаж ----------
def make_collage(image_bytes_list: list) -> BytesIO: ...

# ---------- Запросы к YandexGPT ----------
async def ask_yandex_messages(messages: list, max_tokens: int = 2000, temperature: float = 0.6) -> str: ...
async def ask_ari(user_id: str, question: str) -> str:
    prompt = get_chat_prompt(user_id)
    messages = [{"role": "system", "text": prompt}, {"role": "user", "text": question}]
    return await ask_yandex_messages(messages, max_tokens=500, temperature=0.8)
async def ask_ari_with_context(user_id: str, question: str) -> str:
    prompt = get_chat_prompt(user_id)
    history = list(user_context.get(user_id, []))
    messages = [{"role": "system", "text": prompt}]
    for msg in history:
        if "content" in msg:
            messages.append({"role": msg["role"], "text": msg["content"]})
        elif "text" in msg:
            messages.append(msg)
    messages.append({"role": "user", "text": question})
    return await ask_yandex_messages(messages, max_tokens=500, temperature=0.8)
async def ask_yandex_single(prompt: str, max_tokens: int = 2000, temperature: float = 0.6) -> str: ...

# ---------- Генерация изображений (Yandex Art) ----------
async def generate_image(prompt: str) -> bytes | None: ...

# ---------- Распознавание речи (Yandex STT) ----------
async def recognize_speech(audio_bytes: bytes, lang: str = "ru-RU") -> str: ...

# ---------- Синтез речи (Yandex TTS) ----------
def fix_ari_pronunciation(text: str) -> str: ...
async def synthesize_speech(text: str, lang: str = "ru-RU", emotion: str = "good") -> bytes | None: ...

# ---------- Рамка и стикер ----------
def add_film_frame(image_bytes: bytes) -> BytesIO: ...
def make_sticker(image_bytes: bytes) -> BytesIO: ...

# ---------- Вспомогательные функции обработки ----------
def apply_auto_correction(image_bytes: bytes, params: dict) -> BytesIO:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    # Exposure
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(1 + params.get("exposure", 0) / 2.5)
    # Contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1 + params.get("contrast", 0) / 100)
    # Temperature (грубо через цветовые каналы)
    if "temperature" in params:
        temp_shift = params["temperature"] / 100
        r, g, b = img.split()
        r = r.point(lambda i: min(255, max(0, i + temp_shift * 10)))
        b = b.point(lambda i: min(255, max(0, i - temp_shift * 10)))
        img = Image.merge("RGB", (r, g, b))
    # Vibrance
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1 + params.get("vibrance", 0) / 100)
    # Clarity (резкость)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1 + params.get("clarity", 0) / 50)
    out = BytesIO()
    img.save(out, format="JPEG")
    out.seek(0)
    return out

def apply_xmp_preview(image_bytes: bytes, xmp_str: str) -> BytesIO:
    """Извлекает параметры из XMP-строки и возвращает превью."""
    exposure = re.search(r'crs:Exposure2012="([^"]*)"', xmp_str)
    contrast = re.search(r'crs:Contrast2012="([^"]*)"', xmp_str)
    temp = re.search(r'crs:Temperature="([^"]*)"', xmp_str)
    vibrance = re.search(r'crs:Vibrance="([^"]*)"', xmp_str)
    clarity = re.search(r'crs:Clarity2012="([^"]*)"', xmp_str)
    params = {}
    if exposure: params["exposure"] = float(exposure.group(1))
    if contrast: params["contrast"] = float(contrast.group(1))
    if temp: params["temperature"] = float(temp.group(1))
    if vibrance: params["vibrance"] = float(vibrance.group(1))
    if clarity: params["clarity"] = float(clarity.group(1))
    return apply_auto_correction(image_bytes, params) if params else BytesIO()

# ---------- Эмоциональный анализ ----------
def detect_mood(text: str) -> str: ...

# ---------- Команды ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext): ...

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext): ...

# ... все существующие команды (what, news, podcast, stats, settings, premium, modest, wild, lut, remind, post, frame, makesticker, voicemode, lesson, lang, voice, generate, cancel, broadcast, admin, idea) ...

@dp.message(Command("reference"))
async def cmd_reference(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_reference_source)
    await message.answer("📸 Пришли **исходное** фото, которое будем обрабатывать.")

@dp.message(PhotoStates.waiting_for_reference_source, F.photo)
async def ref_source(message: Message, state: FSMContext):
    await state.update_data(ref_source=message.photo[-1].file_id)
    await state.set_state(PhotoStates.waiting_for_reference_style)
    await message.answer("Теперь пришли **референс**, чей стиль скопируем.")

@dp.message(PhotoStates.waiting_for_reference_style, F.photo)
async def ref_style(message: Message, state: FSMContext):
    data = await state.get_data()
    source_id = data.get("ref_source")
    if not source_id:
        await message.answer("Сначала пришли исходное фото.")
        return
    # Скачиваем оба фото
    source_file = await bot.get_file(source_id)
    style_file = await bot.get_file(message.photo[-1].file_id)
    async with httpx.AsyncClient() as client:
        src_resp = await client.get(f"https://api.telegram.org/file/bot{TOKEN}/{source_file.file_path}")
        stl_resp = await client.get(f"https://api.telegram.org/file/bot{TOKEN}/{style_file.file_path}")
        source_bytes = src_resp.content
        style_bytes = stl_resp.content
    # Получаем описания через Vision
    src_b64 = base64.b64encode(source_bytes).decode()
    stl_b64 = base64.b64encode(style_bytes).decode()
    src_desc = await analyze_objects(src_b64)
    stl_desc = await analyze_objects(stl_b64)
    prompt = (
        f"Исходное фото содержит: {src_desc}\n"
        f"Референс содержит: {stl_desc}\n"
        "Сгенерируй XMP-пресет для Lightroom, который при наложении на исходник "
        "приблизит его по цветам, контрасту и освещению к референсу. "
        "Ответ внутри ```xml ... ```."
    )
    response = await ask_yandex_single(prompt, max_tokens=1500)
    xml_match = re.search(r"```xml\s*(.*?)\s*```", response, re.DOTALL)
    if xml_match:
        preset = BufferedInputFile(xml_match.group(1).encode(), filename="reference.xmp")
        await message.answer_document(preset, caption="🦊 Пресет по образцу готов!")
    else:
        await message.answer("😿 Не удалось создать пресет по образцу.")
    await state.set_state(PhotoStates.waiting_for_photo)

# Команда /studio
@dp.message(Command("studio"))
async def cmd_studio(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_studio_photo)
    await message.answer(LOCALE["ru"]["studio_prompt"])

@dp.message(PhotoStates.waiting_for_studio_photo, F.photo)
async def studio_got_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(studio_photo=photo_id)
    await state.set_state(PhotoStates.waiting_for_studio_effect)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☀️ Тёплый свет", callback_data="studio_warm"),
         InlineKeyboardButton(text="❄️ Холодный свет", callback_data="studio_cold")],
        [InlineKeyboardButton(text="🌈 Неон", callback_data="studio_neon"),
         InlineKeyboardButton(text="💄 Макияж", callback_data="studio_makeup")]
    ])
    await message.answer(LOCALE["ru"]["studio_choose"], reply_markup=kb)

@dp.callback_query(PhotoStates.waiting_for_studio_effect, F.data.startswith("studio_"))
async def apply_studio_effect(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_id = data.get("studio_photo")
    if not file_id:
        await cb.answer("Фото потерялось.", show_alert=True)
        return
    file = await bot.get_file(file_id)
    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file.file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(download_url)
        img_bytes = resp.content
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    effect = cb.data.split("_")[1]
    if effect == "warm":
        r, g, b = img.split()
        r = r.point(lambda i: min(255, i + 30))
        b = b.point(lambda i: max(0, i - 30))
        img = Image.merge("RGB", (r, g, b))
    elif effect == "cold":
        r, g, b = img.split()
        b = b.point(lambda i: min(255, i + 30))
        r = r.point(lambda i: max(0, i - 30))
        img = Image.merge("RGB", (r, g, b))
    elif effect == "neon":
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(1.5)
    elif effect == "makeup":
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(1.2)
        r, g, b = img.split()
        r = r.point(lambda i: min(255, i + 15))
        img = Image.merge("RGB", (r, g, b))
    out = BytesIO()
    img.save(out, format="JPEG")
    out.seek(0)
    await cb.message.answer_photo(FSInputFile(out, filename="studio.jpg"), caption="🦊 Готово! Твоя виртуальная студия.")
    await state.set_state(PhotoStates.waiting_for_photo)
    await cb.answer()

# /aristikers
@dp.message(Command("aristikers"))
async def cmd_aristikers(message: Message):
    await bot.send_chat_action(message.chat.id, "upload_document")
    prompts = [
        "cute fox Ari sticker, waving paw, digital art",
        "fox Ari holding a camera, sticker, colorful",
        "Ari fox with heart eyes, sticker, kawaii",
        "Ari fox saying 'cheese!', photography theme, sticker"
    ]
    sticker_images = []
    for p in prompts:
        img_bytes = await generate_image(p)
        if img_bytes:
            img = Image.open(BytesIO(img_bytes)).convert("RGBA")
            img = img.resize((512, 512), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            sticker_images.append(buf)
    if not sticker_images:
        await message.answer("😿 Не получилось создать стикеры.")
        return
    zip_buf = BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        for i, img_buf in enumerate(sticker_images):
            zf.writestr(f"ari_sticker_{i+1}.png", img_buf.read())
    zip_buf.seek(0)
    await message.answer_document(
        BufferedInputFile(zip_buf.read(), filename="ari_stickers.zip"),
        caption=LOCALE["ru"]["aristikers_done"]
    )

# /adminstats
@dp.message(Command("adminstats"))
async def cmd_adminstats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    total_users = len(all_users)
    total_photos = sum(s.get("photos_analyzed", 0) for s in user_stats.values())
    total_presets = sum(s.get("presets_generated", 0) for s in user_stats.values())
    style_counts = {}
    for s in user_stats.values():
        for st, cnt in s.get("styles_used", {}).items():
            style_counts[st] = style_counts.get(st, 0) + cnt
    popular = ", ".join(f"{k}: {v}" for k, v in sorted(style_counts.items(), key=lambda x: x[1], reverse=True)[:5])
    text = (
        f"👥 Пользователей: {total_users}\n"
        f"📸 Фото проанализировано: {total_photos}\n"
        f"🎨 Пресетов создано: {total_presets}\n"
        f"🏆 Популярные стили: {popular or 'пока нет'}"
    )
    await message.answer(text)

# ---------- Главное меню callback'и ----------
...  # без изменений

# ---------- Обработка фото ----------
@dp.message(F.photo, F.media_group_id == None)
async def handle_single_photo(message: Message, state: FSMContext):
    await process_photo(message, state, single=True)

album_buffer = {}

@dp.message(F.media_group_id)
async def handle_album(message: Message, state: FSMContext):
    gid = message.media_group_id
    if gid not in album_buffer:
        album_buffer[gid] = []
    album_buffer[gid].append(message)
    if len(album_buffer[gid]) == 1:
        await process_photo(message, state, single=False, album_messages=album_buffer[gid])

async def process_photo(message: Message, state: FSMContext, single: bool = True, album_messages: list = None):
    if album_messages is None:
        album_messages = [message]
    await save_user(message.from_user.id)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    current_state = await state.get_state()
    if current_state in [PhotoStates.waiting_for_style, PhotoStates.waiting_for_qa]:
        await message.answer(loc["busy_photo_override"])
    await state.clear()
    if not single:
        await message.answer(loc["album_detected"])
        await state.set_state(PhotoStates.waiting_for_album_style)
    else:
        await state.set_state(PhotoStates.waiting_for_photo)
    photo_msg = album_messages[0]
    photo_id = photo_msg.photo[-1].file_id
    try:
        file_info = await bot.get_file(photo_id)
        if file_info.file_size / 1024 < 5:
            await message.answer(loc["small_photo"])
            return
    except: pass
    await message.answer(loc["analysis_start"])
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        file_info = await bot.get_file(photo_id)
        download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(download_url)
            image_bytes = resp.content
        b64_img = base64.b64encode(image_bytes).decode()
        exif_info = ...
        objects_str = await analyze_objects(b64_img)
        vision_info = ...
        prompt = (exif_info + "\n" + vision_info + "\n" + ANALYSIS_PROMPT) if exif_info or vision_info else ANALYSIS_PROMPT
        analysis = await ask_yandex_single(prompt, max_tokens=2000, temperature=0.4)
        await message.answer(analysis)
        # Извлекаем JSON для автокоррекции
        json_match = re.search(r'```json\s*(.*?)\s*```', analysis, re.DOTALL)
        if json_match:
            try:
                corr_params = json.loads(json_match.group(1))
                await state.update_data(auto_correction=corr_params)
            except:
                pass
        user = str(message.from_user.id)
        if user not in user_stats: user_stats[user] = {}
        user_stats[user]["photos_analyzed"] = user_stats[user].get("photos_analyzed", 0) + 1
        save_stats()
        # ... подготовка all_b64 ...
        if not single:
            await state.update_data(album_b64=all_b64, lang=lang)
            await message.answer(loc["album_choose_style"], reply_markup=get_style_keyboard(lang))
        else:
            await state.update_data(b64_image=b64_img, lang=lang)
            await state.set_state(PhotoStates.waiting_for_style)
            await message.answer("Хочешь применить стиль?", reply_markup=get_style_choice_keyboard(lang))
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await message.answer("😿 Что-то пошло не так.")
        await state.set_state(PhotoStates.waiting_for_photo)

# ---------- Стили и пресеты ----------
... # существующие callback'и

# В process_style_single добавим превью и статистику стилей
@dp.callback_query(PhotoStates.waiting_for_style, F.data.startswith("style_"))
async def process_style_single(cb: CallbackQuery, state: FSMContext):
    global remaining_generations
    chosen = cb.data
    style_info = FILM_PROMPTS.get(chosen, "универсальный стиль")
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    b64 = data.get("b64_image")
    if not b64:
        await cb.message.edit_text("😿 Фото потерялось.")
        return
    if GENERATION_LIMIT > 0 and remaining_generations <= 0:
        await cb.message.edit_text("Ох... аккумулятор сел...")
        return
    await cb.message.edit_text(loc["style_processing"])
    await bot.send_chat_action(cb.message.chat.id, "typing")
    try:
        messages = [ ... ]
        ai_text = await ask_yandex_messages(messages, max_tokens=2000, temperature=0.6)
        if GENERATION_LIMIT > 0: remaining_generations -= 1
        xml_match = re.search(r"```xml\s*(.*?)\s*```", ai_text, re.DOTALL)
        user = str(cb.from_user.id)
        if user not in user_stats: user_stats[user] = {}
        user_stats[user]["presets_generated"] = user_stats[user].get("presets_generated", 0) + 1
        # Счётчик стилей
        user_stats[user]["styles_used"] = user_stats[user].get("styles_used", {})
        user_stats[user]["styles_used"][chosen] = user_stats[user]["styles_used"].get(chosen, 0) + 1
        save_stats()
        if xml_match:
            xml_content = xml_match.group(1).strip()
            # Превью
            try:
                preview = apply_xmp_preview(base64.b64decode(b64), xml_content)
                await cb.message.answer_photo(FSInputFile(preview, filename="preview.jpg"),
                                              caption=LOCALE["ru"]["preview_caption"])
            except Exception as e:
                logger.warning(f"Preview failed: {e}")
            clean = ai_text.replace(xml_match.group(0), "").strip()
            if clean: await cb.message.answer(clean)
            await cb.message.answer_document(BufferedInputFile(xml_content.encode(), filename=f"{chosen}.xmp"),
                                             caption=loc["preset_caption"])
        else:
            await cb.message.answer(ai_text)
        await state.set_state(PhotoStates.waiting_for_qa)
        await cb.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    except Exception as e:
        logger.error(f"Style error: {e}")
        await cb.message.edit_text("😿 Не получилось создать пресет.")
        await state.set_state(PhotoStates.waiting_for_style)
    await cb.answer()

# Для альбома preview не делаем, только статистику стилей
@dp.callback_query(PhotoStates.waiting_for_album_style, F.data.startswith("style_"))
async def process_album_style(cb: CallbackQuery, state: FSMContext):
    ... # без preview, но с styles_used

# ---------- Q&A ----------
@dp.callback_query(PhotoStates.waiting_for_qa, F.data.startswith("qa_"))
async def process_qa(cb: CallbackQuery, state: FSMContext):
    qa = cb.data
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    if qa == "qa_done":
        await cb.message.edit_text(loc["qa_done"])
        await state.set_state(PhotoStates.waiting_for_photo)
    elif qa == "new_analysis":
        await state.set_state(PhotoStates.waiting_for_photo)
        await cb.message.edit_text("🦊 Жду новый кадр!")
    elif qa == "qa_auto_correct":
        corr = data.get("auto_correction")
        b64 = data.get("b64_image")
        if not corr or not b64:
            await cb.answer("Нет данных для коррекции", show_alert=True)
            return
        try:
            image_bytes = base64.b64decode(b64)
            corrected = apply_auto_correction(image_bytes, corr)
            await cb.message.answer_photo(FSInputFile(corrected, filename="corrected.jpg"),
                                          caption="🦊 Магия сработала! Сравни с оригиналом.")
        except Exception as e:
            await cb.answer("Не удалось применить коррекцию.", show_alert=True)
    elif qa == "qa_free_question":
        await state.set_state(PhotoStates.waiting_for_free_question)
        await cb.message.answer("🦊 Задай любой вопрос по этому фото!")
    else:
        answers = { ... }
        await cb.message.answer(answers.get(qa, "🦊 Анализирую..."))
        await cb.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    await cb.answer()

@dp.message(PhotoStates.waiting_for_free_question, F.text)
async def process_free_question(message: Message, state: FSMContext):
    data = await state.get_data()
    b64 = data.get("b64_image")
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    objects_str = await analyze_objects(b64)
    context = f"Пользователь спрашивает про фото, на котором: {objects_str}. Вопрос: {message.text}"
    answer = await ask_ari(str(message.from_user.id), context)
    await message.answer(answer)
    await state.set_state(PhotoStates.waiting_for_qa)

# ---------- Голосовые сообщения ----------
... # без изменений

# ---------- Текстовый чат ----------
@dp.message(F.text & ~F.text.startswith("/"))
async def smart_chat(message: Message, state: FSMContext):
    if not CHAT_ENABLED: return
    # обновим проверку состояний, исключая новые состояния референса
    if await state.get_state() in [PhotoStates.waiting_for_lut_description,
                                   PhotoStates.waiting_for_reference_source,
                                   PhotoStates.waiting_for_reference_style]:
        return
    ... # остальная логика

# ---------- Сохранение последнего фото ----------
...

# ---------- Документы ----------
...

# ---------- Inline-режим ----------
...

# ---------- FastAPI ----------
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
    save_stats()
    save_memory()
    save_all_users()

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
