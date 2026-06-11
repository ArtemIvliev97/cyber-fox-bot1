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
user_context = {}  # user_id -> deque(maxlen=5)
user_stats = {}    # user_id -> dict
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

# ---------- Локализация ----------
LOCALE = {
    "ru": {
        "start": "🦊 Привет! На связи Ари — твой личный объектив в мире классного контента! 📸✨ Я вижу этот мир чертовски красивым и помогу тебе сделать так, чтобы все вокруг тоже это заметили. Можешь сразу прислать фото, и я проанализирую его, или поболтаем — как хочешь! 😉",
        "help": "📖 <b>Инструкция по фокусу</b>\n\n1️⃣ Отправь мне фотографию (или сразу несколько!) — я проанализирую ошибки и дам советы.\n2️⃣ Потом сможешь выбрать плёночный стиль, и я сгенерирую пресет(ы) для Lightroom.\n3️⃣ После анализа можешь задать вопросы по кадру.\n\n🦊 Если я не отвечаю — отправь /start, чтобы разбудить меня снова.\n🐾 Совет: снимай в RAW для максимального качества!",
        "commands_list": (
            "📋 <b>Доступные команды</b>\n\n"
            "/start — Пробудить Ари и начать диалог\n"
            "/help — Инструкция по использованию\n"
            "/commands — Этот список команд\n"
            "/menu — Главное меню с кнопками\n"
            "/what — Что умеет Ари\n"
            "/news — Свежие новости из мира фотографии\n"
            "/podcast — Подкаст «Лисьи байки»\n"
            "/stats — Моя статистика и достижения\n"
            "/frame — Наложить рамку плёнки на фото\n"
            "/makesticker — Сделать стикер из фото\n"
            "/voicemode — Выбрать настроение голоса\n"
            "/lesson — Мини-урок по фотографии\n"
            "/lang — Сменить язык (русский/English)\n"
            "/voice — Проверить голос Ари (если включён)\n"
            "/generate — Сгенерировать изображение по описанию\n"
            "/cancel — Отменить текущее действие\n"
            "/premium — Информация о премиум-возможностях\n"
            "/settings — Настройки (скоро)\n"
            "/admin — Все функции бота (админ)\n"
            "/broadcast — Рассылка (только для админа)"
        ),
        "what_prompt": "Расскажи в двух-трёх игривых предложениях, что ты умеешь как кибер-лисичка Ари: анализировать фото, подбирать плёночные стили, генерировать пресеты для Lightroom, рисовать изображения по описанию, болтать и отвечать голосом. Закончи фразу приглашением прислать фото. Будь эмоциональной, используй эмодзи 🦊📸✨.",
        "news_prompt": "Придумай короткую, но увлекательную новость из мира фотографии (камеры, объективы, выставки, приложения, тренды обработки). Напиши в игривом стиле Ари, с эмодзи, 2-3 предложения. Не используй реальные даты, просто создай правдоподобную и вдохновляющую заметку.",
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
        "main_focus": "📸 Фокус наведён! Присылай своё фото, и я сразу всё расскажу.",
        "main_magic": "✨ Магия ИИ-фильтров",
        "main_crop": "✂️ Функция «Обрезать лишнее» пока в разработке...",
        "main_gallery": "🦊 Твоя галерея пока пуста...",
        "main_energy": "💎 Энергия Ари: сейчас безлимитный доступ.",
        "main_generate": "🎨 Генератор изображений",
        "main_news": "📰 Новости",
        "main_commands": "📋 Команды",
        "lang_switched": "🦊 Язык изменён на русский 🇷🇺",
        "generate_prompt": "🎨 Опиши, что хочешь увидеть, и я нарисую...",
        "generating": "🦊 Рисую...",
        "generated": "✨ Вот что получилось!",
        "generate_error": "😿 Не получилось сгенерировать изображение...",
        "ask_for_photo": "🦊 Чтобы я проанализировала снимок, пришли мне фотографию. Я сразу расскажу, что не так и как исправить!",
        "busy_photo_override": "🦊 Вижу, ты прислал новое фото. Я остановлю предыдущий процесс и начну анализ заново.",
        "new_analysis": "🔄 Новый анализ",
        "voice_unrecognized": "🦊 Не разобрала твой голос. Может, повторишь текстом?",
        "voice_analysis_request": "🦊 Чтобы я проанализировала фото, просто пришли мне картинку, а не говори о ней 😉",
        "where_are_you_reply": "🦊 Тут, тут! Хвостиком виляю из‑за пикселей! Я всегда рядом, когда нужен светлый кадр или просто тёплое слово.",
        "compliments": [
            "Ты сегодня светишься ярче, чем хорошо выставленный баланс белого!",
            "Твой взгляд острее моего объектива — честно‑честно!",
            "Обожаю твои кадры, они даже у пикселей вызывают мурашки.",
            "С тобой любой кадр становится золотым — я проверяла!"
        ],
        "album_detected": "🦊 Ого, целый альбом! Я проанализирую первое фото, а потом подберу стиль для всей серии. Секундочку...",
        "album_choose_style": "🎞️ Выбери стиль, который применить ко всем фото:",
        "news_generating": "🦊 Сейчас покопаюсь в своей ленте... Ловлю свежие новости фотомира!"
    },
    "en": {
        # ... аналогичный перевод
    }
}

# ---------- Стили плёнок ----------
FILM_PROMPTS = {
    "style_kodak_portra": "Kodak Portra 400 (тёплые тона кожи, мягкий контраст, золотистые оттенки)",
    "style_kodak_gold": "Kodak Gold 200 (насыщенные цвета, тёплые оттенки, винтажное настроение)",
    "style_kodak_ektar": "Kodak Ektar 100 (высокая насыщенность, резкость, яркие цвета)",
    "style_kodak_trix": "Kodak Tri-X 400 (классический ч/б стиль, глубокие тени, выраженное зерно)",
    "style_kodak_vision": "Kodak Vision3 250D (кинематографичный стиль, мягкий контраст, естественные тона)",
    "style_fuji_superia": "Fuji Superia 400 (насыщенные зелёные и холодные тона, отличный баланс в тенях)",
    "style_fuji_velvia": "Fuji Velvia 50 (экстремальная насыщенность, сочные цвета, высокая резкость)",
    "style_fuji_provia": "Fuji Provia 100 (естественные цвета, умеренный контраст, гладкая цветопередача)",
    "style_fuji_astia": "Fuji Astia 100 (мягкие пастельные тона, идеально для портретов, низкий контраст)",
    "style_cinestill": "Cinestill 800T (кинематографичный холодный оттенок, неоновые ореолы, киберпанк)",
    "style_hasselblad": "Hasselblad HNCS (натуральные благородные цвета среднего формата, мягкий спад контраста)",
    "style_lomo_redscale": "Lomography Redscale (смещение в красно-оранжевую гамму, эффект засветки)",
    "style_agfa_vista": "Agfa Vista 200 (тёплые, слегка пыльные тона, ретро-стиль 80-х)",
    "style_cinematic": "Cinematic (кинематографический цвет, широкий динамический диапазон)",
    "style_hdr": "HDR (высокая детализация, контраст, насыщенные цвета, без засветов)",
    "style_clean_portrait": "Clean Portrait (мягкая кожа, приятный тон, удаление дефектов)",
    "style_night_city": "Night City (неоновые огни, высокая контрастность, яркие тени)",
}

STYLE_ICONS = {
    "style_kodak_portra": "🎞️", "style_kodak_gold": "✨", "style_kodak_ektar": "🌟",
    "style_kodak_trix": "🖤", "style_kodak_vision": "🎥", "style_fuji_superia": "🌲",
    "style_fuji_velvia": "🌈", "style_fuji_provia": "🌅", "style_fuji_astia": "🌸",
    "style_cinestill": "🌃", "style_hasselblad": "💎", "style_lomo_redscale": "🔴",
    "style_agfa_vista": "📷", "style_cinematic": "🎬", "style_hdr": "🏙️",
    "style_clean_portrait": "👤", "style_night_city": "🌃",
}

# ---------- Системные промпты ----------
SYSTEM_PROMPT = "Ты — Ари, игривая, умная кибер-лисичка, эксперт в фотографии и ИИ. Проанализируй фото, укажи ошибки и дай советы в кокетливом стиле с эмодзи 🦊."
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
    "Для каждого пункта пиши конкретную цифру. Не предлагай пресеты."
)
BASE_PROMPT = (
    "Посмотри на фото и расскажи в стиле Ари, как обработать его в стиле {style_info}. "
    "Сгенерируй XMP-пресет для Lightroom Mobile внутри тегов ```xml ... ```.\n"
    "Только параметры: Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Temperature, Tint, Vibrance, Saturation, Clarity, ToneCurve."
)
CHAT_PROMPT = (
    "Ты — Ари, живая кибер-лисичка с характером. Будь эмоциональной, остроумной, используй эмодзи 🦊📸✨. "
    "Учитывай историю диалога (если есть), чтобы отвечать связно."
)

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
    # ... (как в предыдущей версии)
    pass

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
async def ask_yandex(prompt: str, max_tokens: str = "2000", temperature: float = 0.6) -> str:
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": max_tokens},
        "messages": [{"role": "system", "text": SYSTEM_PROMPT}, {"role": "user", "text": prompt}]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion", headers=headers, json=body, timeout=60.0)
        if resp.status_code == 200:
            return resp.json()["result"]["alternatives"][0]["message"]["text"]
        else:
            logger.error(f"Yandex API error: {resp.status_code}")
            return "🦊 Что-то пошло не так с моими кибер‑лапками..."
    except Exception as e:
        logger.error(f"Yandex request failed: {e}")
        return "🦊 Хвост запутался в проводах!"

async def ask_ari(question: str) -> str:
    # используется для простых запросов без контекста (например, what)
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": "500"},
        "messages": [{"role": "system", "text": CHAT_PROMPT}, {"role": "user", "text": question}]
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

async def generate_image(prompt: str) -> bytes | None:
    # ... без изменений
    pass

async def recognize_speech(audio_bytes: bytes, lang: str = "ru-RU") -> str:
    # ... без изменений
    pass

def fix_ari_pronunciation(text: str) -> str:
    return re.sub(r'\bАри\b', 'А+ри', text)

async def synthesize_speech(text: str, lang: str = "ru-RU", emotion: str = "good") -> bytes | None:
    # emotion: good, sad, neutral
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

async def save_user(user_id: int):
    all_users.add(user_id)

# ---------- Функции рамки и стикера ----------
def add_film_frame(image_bytes: bytes) -> BytesIO:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    width, height = img.size
    frame_width = 30
    new_img = Image.new("RGB", (width + 2*frame_width, height + 2*frame_width), "black")
    new_img.paste(img, (frame_width, frame_width))
    draw = ImageDraw.Draw(new_img)
    hole_radius = 4
    for y in range(frame_width, height + frame_width, 15):
        for x in (5, width + 2*frame_width - 5):
            draw.ellipse((x-hole_radius, y-hole_radius, x+hole_radius, y+hole_radius), fill="white")
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
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
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

# ---------- Главное меню и другие обработчики (как раньше) ----------
# (Остальные callback'и главного меню, обработка фото, альбомов, стилей, Q&A, генератор, документы, голосовые и чат)

# ---------- Обновлённый обработчик текстового чата с контекстом ----------
@dp.message(F.text & ~F.text.startswith("/"))
async def smart_chat(message: Message, state: FSMContext):
    if not CHAT_ENABLED: return
    user_id = str(message.from_user.id)
    if user_id not in user_context:
        user_context[user_id] = deque(maxlen=5)
    user_context[user_id].append({"role": "user", "text": message.text})

    # Проверки на "где ты", "что умеешь" и т.д. (можно добавить)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    if any(phrase in message.text.lower() for phrase in ["ты где", "где ты", "покажись", "ари, ты тут"]):
        await message.answer(loc["where_are_you_reply"])
        return

    analysis_keywords = ["проанализируй", "разбери фото", "оцени фото", "что с фото",
                         "проверь снимок", "скажи про фотку", "анализ", "дай совет по фото"]
    if any(word in message.text.lower() for word in analysis_keywords):
        await message.answer(loc["ask_for_photo"])
        return

    current_state = await state.get_state()
    if current_state == PhotoStates.waiting_for_photo:
        await bot.send_chat_action(message.chat.id, "typing")
        await asyncio.sleep(random.uniform(0.5, 2.0))
        reply = await ask_ari_with_context(user_id, message.text)
        user_context[user_id].append({"role": "assistant", "text": reply})
        if random.random() < 0.2:
            comp = random.choice(LOCALE[lang]["compliments"])
            reply = comp + "\n" + reply
        await message.answer(reply)
        return

    if current_state is not None:
        return

    if any(phrase in message.text.lower() for phrase in ["что ты умеешь", "что умеешь", "что можешь"]):
        prompt = LOCALE[lang]["what_prompt"]
        reply = await ask_ari(prompt)
        await message.answer(reply)
        return
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(random.uniform(0.5, 2.0))
    reply = await ask_ari_with_context(user_id, message.text)
    user_context[user_id].append({"role": "assistant", "text": reply})
    if random.random() < 0.2:
        comp = random.choice(LOCALE[lang]["compliments"])
        reply = comp + "\n" + reply
    await message.answer(reply)

# ---------- Inline-режим ----------
@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    result = InlineQueryResultArticle(
        id="1",
        title="Открыть Ари",
        description="Начать диалог с кибер-лисичкой",
        input_message_content=InputTextMessageContent(message_text="/start"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Перейти в чат", url=f"https://t.me/{(await bot.me()).username}")]
        ])
    )
    await inline_query.answer([result], cache_time=1)

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
