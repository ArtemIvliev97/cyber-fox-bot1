import base64, logging, os, re, httpx, asyncio, random, io, zipfile, json
from collections import deque
from datetime import datetime
from contextlib import asynccontextmanager
from io import BytesIO

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
from PIL import Image, ImageDraw, ImageFont

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

# ---------- Хранилища ----------
user_context = {}
user_stats = {}
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

LESSONS = [
    {
        "title": "Основы композиции",
        "steps": [
            "Правило третей...",
            "Ведущие линии...",
            "Симметрия...",
            "Негативное пространство...",
            "Отлично! Попробуй применить в следующем кадре. 📸"
        ]
    },
    {
        "title": "Работа со светом",
        "steps": [
            "Золотой час...",
            "Синий час...",
            "Контровой свет...",
            "Заполняющий свет...",
            "Супер! Теперь ты знаешь, как приручить свет. ✨"
        ]
    }
]

# ---------- Локализация (полная русская версия) ----------
LOCALE = {
    "ru": {
        "start": "🦊 Привет! На связи Ари — твой личный объектив...",
        "help": "📖 Инструкция...",
        "commands_list": "/start ... /voice ...",
        "what_prompt": "Расскажи...",
        "news_prompt": "Придумай новость...",
        "podcast_intro": "🎙️ Подкаст...",
        "podcast_prompt": "Расскажи подкаст...",
        "frame_added": "🦊 Рамка плёнки добавлена!",
        "frame_prompt": "Пришли фото для рамки.",
        "sticker_done": "🦊 Вот твой стикер!",
        "sticker_prompt": "Пришли фото для стикера.",
        "voice_emotion_set": "✅ Тембр голоса изменён на: {emotion}.",
        "voice_emotion_prompt": "Выбери настроение голоса:",
        "stats_text": "📊 Статистика...",
        "lesson_start": "🎓 Урок: {title}.",
        "lesson_next": "Далее ➡️",
        "lesson_prev": "⬅️ Назад",
        "lesson_finish": "✅ Завершить",
        "admin_features": "🛠 Все функции бота...",
        "choose_style": "🎞️ Выбери стиль",
        "skip_style": "✅ Разбор завершён!",
        "all_styles": "📋 Все стили",
        "analysis_start": "🦊 Сканирую...",
        "small_photo": "Ой, маленькая картинка...",
        "document_error": "Упс! Это файл, а не фото.",
        "style_processing": "Ловлю фокус...",
        "preset_caption": "🦊 Твой пресет!",
        "album_preset_caption": "🦊 Архив пресетов.",
        "qa_choose": "Есть вопросы?",
        "qa_done": "✅ Завершено.",
        "main_focus": "📸 Фокус",
        "main_magic": "✨ Магия",
        "main_crop": "✂️ Обрезка",
        "main_gallery": "🦊 Галерея",
        "main_energy": "💎 Энергия",
        "main_generate": "🎨 Генератор",
        "main_news": "📰 Новости",
        "main_commands": "📋 Команды",
        "lang_switched": "🦊 Язык изменён",
        "generate_prompt": "Опиши изображение...",
        "generating": "🦊 Рисую...",
        "generated": "✨ Готово!",
        "generate_error": "😿 Ошибка генерации.",
        "ask_for_photo": "🦊 Пришли фото!",
        "busy_photo_override": "🦊 Новое фото, начинаю заново.",
        "new_analysis": "🔄 Новый анализ",
        "voice_unrecognized": "🦊 Не разобрала голос.",
        "voice_analysis_request": "🦊 Пришли фото, а не говори.",
        "where_are_you_reply": "🦊 Я тут!",
        "compliments": ["Ты светишься!", "Острый взгляд!", "Обожаю твои кадры!"],
        "album_detected": "🦊 Альбом! Анализирую...",
        "album_choose_style": "🎞️ Стиль для альбома:",
        "news_generating": "🦊 Ищу новости..."
    }
}

# ---------- Стили и иконки (полные) ----------
FILM_PROMPTS = { ... }  # скопировать из предыдущей полной версии
STYLE_ICONS = { ... }

# ---------- Системные промпты ----------
SYSTEM_PROMPT = "Ты — Ари..."
ANALYSIS_PROMPT = "Посмотри на фото..."
BASE_PROMPT = "Сгенерируй пресет..."
CHAT_PROMPT = "Ты — Ари, живая кибер-лисичка..."

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
all_users = set()

# ---------- Клавиатуры ----------
def get_main_menu_keyboard(lang="ru"):
    loc = LOCALE.get(lang, LOCALE["ru"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 " + loc["main_focus"], callback_data="main_focus")],
        [InlineKeyboardButton(text="✨ " + loc["main_magic"], callback_data="main_magic")],
        [InlineKeyboardButton(text="🎙️ Подкаст", callback_data="podcast")],
        [InlineKeyboardButton(text="🎞️ Рамка", callback_data="frame")],
        [InlineKeyboardButton(text="🎓 Урок", callback_data="lesson")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="🎨 Генератор", callback_data="main_generate")],
        [InlineKeyboardButton(text="📋 Команды", callback_data="main_commands")],
    ])

def get_style_keyboard(lang="ru", selected_styles=None):
    buttons = []
    if selected_styles:
        for style_id in selected_styles:
            if style_id in FILM_PROMPTS:
                icon = STYLE_ICONS.get(style_id, "🎞️")
                name_parts = style_id.replace("style_", "").split("_")
                display_name = " ".join(part.capitalize() for part in name_parts)
                label = f"{icon} {display_name}"
                buttons.append(InlineKeyboardButton(text=label, callback_data=style_id))
        buttons.append(InlineKeyboardButton(text=LOCALE[lang]["all_styles"], callback_data="all_styles"))
        keyboard_rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    else:
        for style_id in FILM_PROMPTS:
            icon = STYLE_ICONS.get(style_id, "🎞️")
            name_parts = style_id.replace("style_", "").split("_")
            display_name = " ".join(part.capitalize() for part in name_parts)
            label = f"{icon} {display_name}"
            buttons.append(InlineKeyboardButton(text=label, callback_data=style_id))
        keyboard_rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

def get_style_choice_keyboard(lang="ru"):
    loc = LOCALE.get(lang, LOCALE["ru"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=loc["choose_style"], callback_data="choose_style"),
         InlineKeyboardButton(text=loc["skip_style"], callback_data="skip_style")]
    ])

def get_qa_keyboard(lang="ru"):
    loc = LOCALE.get(lang, LOCALE["ru"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌡️ Баланс белого", callback_data="qa_wb"),
         InlineKeyboardButton(text="⛅ Пересветы", callback_data="qa_sky")],
        [InlineKeyboardButton(text="🌑 Тени", callback_data="qa_shadows"),
         InlineKeyboardButton(text="📐 Кадрирование", callback_data="qa_crop")],
        [InlineKeyboardButton(text="👤 Не вижу лицо", callback_data="qa_face"),
         InlineKeyboardButton(text="🛑 Завершить", callback_data="qa_done")],
        [InlineKeyboardButton(text=loc["new_analysis"], callback_data="new_analysis")]
    ])

def get_voice_emotion_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😊 Радостный", callback_data="voice_happy"),
         InlineKeyboardButton(text="😢 Грустный", callback_data="voice_sad")],
        [InlineKeyboardButton(text="🕵️ Загадочный", callback_data="voice_mysterious"),
         InlineKeyboardButton(text="😌 Успокаивающий", callback_data="voice_calm")],
    ])

def get_lesson_keyboard(step, total):
    buttons = []
    if step > 0:
        buttons.append(InlineKeyboardButton(text=LOCALE["ru"]["lesson_prev"], callback_data="lesson_prev"))
    if step < total - 1:
        buttons.append(InlineKeyboardButton(text=LOCALE["ru"]["lesson_next"], callback_data="lesson_next"))
    else:
        buttons.append(InlineKeyboardButton(text=LOCALE["ru"]["lesson_finish"], callback_data="lesson_finish"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

# ---------- Запросы к Yandex ----------
async def ask_yandex(prompt, max_tokens="2000", temperature=0.6):
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": max_tokens},
        "messages": [{"role": "system", "text": SYSTEM_PROMPT}, {"role": "user", "text": prompt}]
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion", headers=headers, json=body, timeout=60.0)
    if resp.status_code == 200:
        return resp.json()["result"]["alternatives"][0]["message"]["text"]
    else:
        logger.error(f"Yandex API error: {resp.status_code}")
        return "🦊 Ошибка..."

async def ask_ari(question: str) -> str:
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": "500"},
        "messages": [{"role": "system", "text": CHAT_PROMPT}, {"role": "user", "text": question}]
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion", headers=headers, json=body, timeout=30.0)
    if resp.status_code == 200:
        return resp.json()["result"]["alternatives"][0]["message"]["text"]
    else:
        return "🦊 Что-то я запуталась..."

async def ask_ari_with_context(user_id: str, question: str) -> str:
    history = list(user_context.get(user_id, []))
    messages = [{"role": "system", "text": CHAT_PROMPT}]
    for msg in history:
        messages.append(msg)
    messages.append({"role": "user", "text": question})
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": "500"},
        "messages": messages
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion", headers=headers, json=body, timeout=30.0)
    if resp.status_code == 200:
        return resp.json()["result"]["alternatives"][0]["message"]["text"]
    else:
        return "🦊 Что-то я запуталась..."

async def recognize_speech(audio_bytes: bytes, lang: str = "ru-RU") -> str:
    url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
    params = {"lang": lang, "format": "oggopus"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, params=params, content=audio_bytes, timeout=30.0)
    if resp.status_code == 200:
        return resp.json().get("result", "")
    else:
        logger.error(f"STT error: {resp.status_code}")
        return ""

def fix_ari_pronunciation(text: str) -> str:
    return re.sub(r'\bАри\b', 'А+ри', text)

async def synthesize_speech(text: str, lang: str = "ru-RU", emotion: str = "good") -> bytes | None:
    cute_prefixes = [
        "Ой! ", "Хм-м... ", "Уи-и! ", "Слушай... ",
        "Ну что... ", "Эй! ", "", "",
        "Охохо! ", "Мрр-мяу?.. шучу, я же лиса! ", "Смотри-ка... "
    ]
    prefix = random.choice(cute_prefixes)
    full_text = prefix + text
    voice = random.choice(["alena", "oksana"])
    url = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
    params = {
        "text": full_text,
        "lang": lang,
        "voice": voice,
        "emotion": emotion,
        "speed": str(round(random.uniform(0.85, 0.95), 2)),
        "format": "oggopus",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, data=params, timeout=30.0)
    if resp.status_code == 200:
        return resp.content
    else:
        logger.error(f"TTS error: {resp.status_code}")
        # fallback без emotion
        params.pop("emotion", None)
        params["speed"] = "1.0"
        async with httpx.AsyncClient() as client2:
            resp2 = await client2.post(url, headers=headers, data=params, timeout=30.0)
        if resp2.status_code == 200:
            return resp2.content
        return None

async def save_user(user_id: int):
    all_users.add(user_id)

# ---------- Рамка и стикер ----------
def add_film_frame(image_bytes: bytes) -> BytesIO:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    fw = 30
    new_img = Image.new("RGB", (w+2*fw, h+2*fw), "black")
    new_img.paste(img, (fw, fw))
    draw = ImageDraw.Draw(new_img)
    r = 4
    for y in range(fw, h+fw, 15):
        for x in (5, w+2*fw-5):
            draw.ellipse((x-r, y-r, x+r, y+r), fill="white")
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except:
        font = ImageFont.load_default()
    draw.text((5,5), "Ари", fill="orange", font=font)
    out = BytesIO()
    new_img.save(out, format="JPEG")
    out.seek(0)
    return out

def make_sticker(image_bytes: bytes) -> BytesIO:
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    s = min(img.size)
    left = (img.width - s)/2
    top = (img.height - s)/2
    img = img.crop((left, top, left+s, top+s))
    img = img.resize((512,512), Image.LANCZOS)
    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out

# ---------- Обработчики команд ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    lang = "ru"
    await state.update_data(lang=lang)
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer(LOCALE[lang]["start"], reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("voice"))
async def cmd_voice(message: Message, state: FSMContext):
    if not VOICE_ENABLED:
        await message.answer("Голос отключён.")
        return
    # используем emotion из состояния, если есть
    data = await state.get_data()
    emotion = data.get("voice_emotion", "good")
    phrases = [
        "Привет! Я Ари, и мой голос стал ещё милее!",
        "Ой, кажется, у меня мурашки по лапкам от твоего внимания!",
        "Сегодня отличный день, чтобы сделать крутой кадр. Ты готов?"
    ]
    for phrase in phrases:
        corrected = fix_ari_pronunciation(phrase)
        voice_bytes = await synthesize_speech(corrected, lang="ru-RU", emotion=emotion)
        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="ari_test.ogg")
            await message.answer_voice(voice_file)
        else:
            await message.answer("😿 Не получилось синтезировать голос.")

@dp.message(Command("podcast"))
async def cmd_podcast(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await bot.send_chat_action(message.chat.id, "record_voice")
    prompt = LOCALE[lang]["podcast_prompt"]
    podcast_text = await ask_ari(prompt)
    voice_bytes = await synthesize_speech(podcast_text, lang="ru-RU", emotion="good")
    if voice_bytes:
        await message.answer_voice(BufferedInputFile(voice_bytes, filename="podcast.ogg"))
    await message.answer(podcast_text)

# ... (остальные команды: help, commands, menu, lang, what, news, cancel, broadcast, stats, frame, makesticker, voicemode, lesson, admin, generate, а также обработчики фото, альбомов, стилей, Q&A, документов, голосовых сообщений, текстового чата с контекстом, инлайн)

# Важно: голосовой обработчик
@dp.message(F.voice)
async def voice_handler(message: Message, state: FSMContext):
    if not CHAT_ENABLED or not VOICE_ENABLED:
        return
    await save_user(message.from_user.id)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    lang_code = "ru-RU" if lang == "ru" else "en-US"
    emotion = data.get("voice_emotion", "good")

    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    audio_bytes = file_bytes.read()

    await bot.send_chat_action(message.chat.id, "typing")
    text = await recognize_speech(audio_bytes, lang_code)
    if not text:
        await message.answer(loc["voice_unrecognized"])
        return

    # проверка на анализ
    analysis_keywords = ["проанализируй", "разбери фото", "оцени фото", "что с фото",
                         "проверь снимок", "скажи про фотку", "анализ", "дай совет по фото"]
    if any(word in text.lower() for word in analysis_keywords):
        await message.answer(loc["voice_analysis_request"])
        return

    reply_text = await ask_ari_with_context(str(message.from_user.id), text)
    corrected_reply = fix_ari_pronunciation(reply_text)
    voice_bytes = await synthesize_speech(corrected_reply, lang_code, emotion=emotion)
    if voice_bytes:
        await message.answer_voice(BufferedInputFile(voice_bytes, filename="ari_voice.ogg"))
    await message.answer(reply_text)

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
