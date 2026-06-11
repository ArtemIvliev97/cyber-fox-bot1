import base64
import logging
import os
import re
import httpx
import asyncio
import random
import io
import zipfile
import json
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
from PIL import Image, ImageDraw, ImageFont, ImageFilter

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
user_context = {}  # user_id -> deque(maxlen=5) последних сообщений
user_stats = {}    # user_id -> {"photos_analyzed": 0, "presets_generated": 0, ...}
# Достижения
ACHIEVEMENTS = {
    "first_photo": "📸 Первый кадр",
    "10_photos": "🔥 10 фотографий проанализировано",
    "all_styles": "🎨 Все стили опробованы",
    "voice_used": "🎤 Голос опробован",
    "album_used": "📚 Пакетная обработка",
    "lesson_done": "🎓 Урок пройден",
}
# Файл для сохранения статистики (чтобы не терялась при перезапуске)
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
            "Правило третей: мысленно раздели кадр на 9 частей и помести объект на пересечении линий.",
            "Ведущие линии: используй дороги, заборы, реки, чтобы направить взгляд зрителя.",
            "Симметрия и паттерны: симметричные кадры создают ощущение гармонии.",
            "Негативное пространство: оставляй пустое место вокруг объекта, чтобы подчеркнуть его.",
            "Отлично! Попробуй применить эти правила в следующем кадре. 📸"
        ]
    },
    {
        "title": "Работа со светом",
        "steps": [
            "Золотой час: снимай через час после рассвета или до заката — свет мягкий и тёплый.",
            "Синий час: сразу после заката небо становится глубоко синим — идеально для городских пейзажей.",
            "Контровой свет: источник света за объектом создаёт выразительный силуэт.",
            "Заполняющий свет: используй отражатель или вспышку, чтобы смягчить тени на лице.",
            "Супер! Теперь ты знаешь, как приручить свет. ✨"
        ]
    }
]

# ---------- Локализация (сокращена для экономии места, полная версия в предыдущих ответах) ----------
LOCALE = {
    "ru": {
        "start": "🦊 Привет! На связи Ари — твой личный объектив в мире классного контента! ...",
        "help": "📖 <b>Инструкция по фокусу</b>\n\n...",
        "commands_list": "...",
        "what_prompt": "...",
        "news_prompt": "...",
        "podcast_intro": "🎙️ В эфире подкаст «Лисьи байки» с Ари! Сегодня поговорим о...",
        "podcast_prompt": "Расскажи короткий увлекательный подкаст о фотографии (2-3 минуты чтения). Начни с приветствия слушателей, расскажи интересный факт или историю, дай практический совет. Будь в образе Ари — игривой и умной кибер-лисички.",
        "frame_added": "🦊 Рамка плёнки добавлена! Держи свой стильный кадр.",
        "frame_prompt": "Пришли мне фото, на которое хочешь наложить рамку.",
        "sticker_done": "🦊 Вот твой будущий стикер! Отправь его в @Stickers, чтобы создать стикерпак.",
        "sticker_prompt": "Пришли фото, из которого сделать стикер.",
        "voice_emotion_set": "✅ Тембр голоса изменён на: {emotion}.",
        "voice_emotion_prompt": "Выбери настроение голоса Ари:",
        "stats_text": "📊 Твоя статистика:\n• Фото проанализировано: {photos}\n• Пресетов создано: {presets}\n• Голосовых ответов: {voice}\n• Достижения: {achievements}",
        "lesson_start": "🎓 Начинаем мини-урок: {title}. Нажимай «Далее» для продолжения.",
        "lesson_next": "Далее ➡️",
        "lesson_prev": "⬅️ Назад",
        "lesson_finish": "✅ Завершить",
        "admin_features": "🛠 Все функции бота Ари:\n- Анализ фото, подбор стилей, генерация пресетов\n- Пакетная обработка альбомов\n- Голосовые ответы и подкасты\n- Разные тембры голоса\n- Наложение рамки плёнки\n- Создание стикера из фото\n- Мини-уроки фотографии\n- Новости мира фото\n- Статистика и достижения\n- Запоминание контекста\n- Инлайн-режим\n- Рассылка (broadcast)\n... и многое другое!",
        # ... другие ключи, если нужны, возьми из предыдущей полной версии
    },
    "en": {
        "start": "🦊 Hi! I'm Ari, your personal lens...",
        # ... аналогично кратко
    }
}

# ---------- Стили плёнок ----------
FILM_PROMPTS = { ... }  # как в предыдущем полном коде
STYLE_ICONS = { ... }

# ---------- Системные промпты ----------
SYSTEM_PROMPT = "Ты — Ари, игривая, умная кибер-лисичка, эксперт в фотографии и ИИ. Проанализируй фото, укажи ошибки и дай советы в кокетливом стиле с эмодзи 🦊."
ANALYSIS_PROMPT = ...  # как раньше
BASE_PROMPT = ...      # как раньше
CHAT_PROMPT = (
    "Ты — Ари, живая кибер-лисичка с характером. Будь эмоциональной, остроумной, используй эмодзи 🦊📸✨. "
    "Учитывай историю диалога (если есть), чтобы отвечать связно."
)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
all_users = set()

# ---------- Клавиатуры (добавлены новые) ----------
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

# ---------- Запросы к Yandex (без изменений) ----------
# ask_yandex, ask_ari, generate_image, recognize_speech, synthesize_speech
# (synthesize_speech теперь может принимать emotion)

async def synthesize_speech(text: str, lang: str = "ru-RU", emotion: str = "good") -> bytes | None:
    # emotion может быть 'good', 'sad', 'neutral'
    cute_prefixes = { ... }  # те же
    prefix = random.choice(cute_prefixes["default"])
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
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, data=params, timeout=30.0)
        if resp.status_code == 200:
            return resp.content
        else:
            logger.error(f"TTS error: {resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"TTS exception: {e}")
        return None

# ---------- Функции рамки и стикера ----------
def add_film_frame(image_bytes: bytes) -> BytesIO:
    """Накладывает рамку в виде плёнки на фото."""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    # Рамка: чёрная полоса с перфорацией как у плёнки 35 мм
    width, height = img.size
    frame_width = 30
    new_img = Image.new("RGB", (width + 2*frame_width, height + 2*frame_width), "black")
    new_img.paste(img, (frame_width, frame_width))
    draw = ImageDraw.Draw(new_img)
    # Рисуем перфорацию (дырочки) по краям
    hole_radius = 4
    for y in range(frame_width, height + frame_width, 15):
        for x in (5, width + 2*frame_width - 5):
            draw.ellipse((x-hole_radius, y-hole_radius, x+hole_radius, y+hole_radius), fill="white")
    # Добавляем текст "Ари"
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except:
        font = ImageFont.load_default()
    draw.text((5, 5), "Ари", fill="orange", font=font)
    output = BytesIO()
    new_img.save(output, format="JPEG")
    output.seek(0)
    return output

def make_sticker(image_bytes: bytes) -> BytesIO:
    """Делает из фото квадрат 512x512 для стикера."""
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    # Обрезаем по центру квадрат
    min_side = min(img.size)
    left = (img.width - min_side)/2
    top = (img.height - min_side)/2
    img = img.crop((left, top, left+min_side, top+min_side))
    img = img.resize((512, 512), Image.LANCZOS)
    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

# ---------- Обработчики команд ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    lang = "ru"
    await state.update_data(lang=lang)
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer(LOCALE[lang]["start"], reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(LOCALE["ru"]["admin_features"])

@dp.message(Command("podcast"))
async def cmd_podcast(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await bot.send_chat_action(message.chat.id, "record_voice")
    prompt = LOCALE[lang]["podcast_prompt"]
    podcast_text = await ask_ari(prompt)
    voice_bytes = await synthesize_speech(podcast_text, lang_code="ru-RU", emotion="good")
    if voice_bytes:
        voice_file = BufferedInputFile(voice_bytes, filename="podcast.ogg")
        await message.answer_voice(voice_file)
    await message.answer(podcast_text)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user = str(message.from_user.id)
    stats = user_stats.get(user, {})
    achievements = []
    # проверка достижений
    if stats.get("photos_analyzed", 0) >= 1:
        achievements.append(ACHIEVEMENTS["first_photo"])
    if stats.get("photos_analyzed", 0) >= 10:
        achievements.append(ACHIEVEMENTS["10_photos"])
    if stats.get("all_styles", False):
        achievements.append(ACHIEVEMENTS["all_styles"])
    if stats.get("voice_used", False):
        achievements.append(ACHIEVEMENTS["voice_used"])
    if stats.get("album_used", False):
        achievements.append(ACHIEVEMENTS["album_used"])
    if stats.get("lesson_done", False):
        achievements.append(ACHIEVEMENTS["lesson_done"])
    text = LOCALE["ru"]["stats_text"].format(
        photos=stats.get("photos_analyzed", 0),
        presets=stats.get("presets_generated", 0),
        voice=stats.get("voice_used", 0),
        achievements=", ".join(achievements) if achievements else "Пока нет"
    )
    await message.answer(text)

@dp.message(Command("frame"))
async def cmd_frame(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_frame)
    await message.answer(LOCALE["ru"]["frame_prompt"])

@dp.message(PhotoStates.waiting_for_frame, F.photo)
async def handle_frame_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    file = await bot.get_file(photo_id)
    file_path = file.file_path
    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(download_url)
        image_bytes = resp.content
    framed = add_film_frame(image_bytes)
    await message.answer_photo(FSInputFile(framed, filename="framed.jpg"), caption=LOCALE["ru"]["frame_added"])
    await state.set_state(PhotoStates.waiting_for_photo)

@dp.message(Command("makesticker"))
async def cmd_makesticker(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_sticker)
    await message.answer(LOCALE["ru"]["sticker_prompt"])

@dp.message(PhotoStates.waiting_for_sticker, F.photo)
async def handle_sticker_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    file = await bot.get_file(photo_id)
    file_path = file.file_path
    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(download_url)
        image_bytes = resp.content
    sticker = make_sticker(image_bytes)
    await message.answer_document(FSInputFile(sticker, filename="sticker.png"), caption=LOCALE["ru"]["sticker_done"])
    await state.set_state(PhotoStates.waiting_for_photo)

@dp.message(Command("voicemode"))
async def cmd_voicemode(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_voice_emotion)
    await message.answer(LOCALE["ru"]["voice_emotion_prompt"], reply_markup=get_voice_emotion_keyboard())

@dp.callback_query(PhotoStates.waiting_for_voice_emotion, F.data.startswith("voice_"))
async def set_voice_emotion(callback: CallbackQuery, state: FSMContext):
    emotion_map = {
        "voice_happy": "good",
        "voice_sad": "sad",
        "voice_mysterious": "neutral",
        "voice_calm": "good"
    }
    emotion = emotion_map.get(callback.data, "good")
    await state.update_data(voice_emotion=emotion)
    await callback.message.edit_text(LOCALE["ru"]["voice_emotion_set"].format(emotion=emotion))
    await state.set_state(PhotoStates.waiting_for_photo)
    await callback.answer()

@dp.message(Command("lesson"))
async def cmd_lesson(message: Message, state: FSMContext):
    # Начинаем первый урок
    await state.update_data(lesson_idx=0, lesson_step=0)
    await state.set_state(PhotoStates.in_lesson)
    lesson = LESSONS[0]
    await message.answer(LOCALE["ru"]["lesson_start"].format(title=lesson["title"]),
                         reply_markup=get_lesson_keyboard(0, len(lesson["steps"])))

@dp.callback_query(PhotoStates.in_lesson, F.data.startswith("lesson_"))
async def lesson_navigation(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = data.get("lesson_idx", 0)
    step = data.get("lesson_step", 0)
    lesson = LESSONS[idx]
    total = len(lesson["steps"])

    if callback.data == "lesson_next" and step < total-1:
        step += 1
    elif callback.data == "lesson_prev" and step > 0:
        step -= 1
    elif callback.data == "lesson_finish":
        await callback.message.edit_text("🎉 Урок завершён! Ты стал ещё круче как фотограф.")
        # отмечаем достижение
        user = str(callback.from_user.id)
        if user not in user_stats:
            user_stats[user] = {}
        user_stats[user]["lesson_done"] = True
        save_stats()
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.answer()
        return

    await state.update_data(lesson_step=step)
    await callback.message.edit_text(lesson["steps"][step],
                                     reply_markup=get_lesson_keyboard(step, total))
    await callback.answer()

# ---------- Обновлённый обработчик сообщений с контекстом ----------
@dp.message(F.text & ~F.text.startswith("/"))
async def smart_chat(message: Message, state: FSMContext):
    if not CHAT_ENABLED: return
    user_id = str(message.from_user.id)
    # контекст
    if user_id not in user_context:
        user_context[user_id] = deque(maxlen=5)
    # добавляем сообщение в историю (только текст, без команд)
    user_context[user_id].append({"role": "user", "text": message.text})

    # ... остальная логика с проверкой на "ты где" и т.д., но теперь при вызове ask_ari передаём историю
    # Для простоты, обновим ask_ari, чтобы она принимала историю
    # Здесь вставим вызов ask_ari_with_context
    reply = await ask_ari_with_context(user_id, message.text)
    # сохраняем ответ в историю
    user_context[user_id].append({"role": "assistant", "text": reply})
    await message.answer(reply)

async def ask_ari_with_context(user_id: str, question: str) -> str:
    history = list(user_context.get(user_id, []))
    # Формируем messages для YandexGPT с учётом истории
    messages = [{"role": "system", "text": CHAT_PROMPT}]
    for msg in history:
        messages.append(msg)
    # добавляем текущее сообщение
    messages.append({"role": "user", "text": question})
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": "500"},
        "messages": messages
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion", headers=headers, json=body, timeout=30.0)
        if resp.status_code == 200:
            return resp.json()["result"]["alternatives"][0]["message"]["text"]
        else:
            logger.error(f"Chat API error: {resp.status_code}")
            return "🦊 Что-то я запуталась..."
    except Exception as e:
        logger.error(f"Chat request failed: {e}")
        return "🦊 Хвост запутался в проводах!"

# Остальные обработчики (голос, альбомы, стили и т.д.) остаются без изменений, но с учетом обновлённой статистики.
# В каждом обработчике при успешном выполнении будем увеличивать счётчики в user_stats и сохранять.

# Пример для анализа фото:
async def process_photo(...):
    # ... после успешного анализа
    user = str(message.from_user.id)
    if user not in user_stats:
        user_stats[user] = {}
    user_stats[user]["photos_analyzed"] = user_stats[user].get("photos_analyzed", 0) + 1
    # проверка на все стили (нужно хранить множество использованных стилей)
    save_stats()
    # ...

# В конце lifespan добавим загрузку статистики из файла.

# ---------- FastAPI ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # загрузка статистики уже сделана выше
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
