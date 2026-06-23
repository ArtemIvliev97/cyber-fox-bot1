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
    waiting_for_reminder = State()
    waiting_for_lut_description = State()
    waiting_for_reference = State()
    waiting_for_collage = State()

# ---------- Хранилища ----------
user_memory = {}
user_personality = {}   # "wild" или "modest"
MEMORY_FILE = "user_memory.json"
if os.path.exists(MEMORY_FILE):
    try:
        data = json.load(open(MEMORY_FILE, "r"))
        if isinstance(data, dict):
            # Старые данные могли быть просто словарём, теперь храним два словаря
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

# ---------- Локализация (усиленная) ----------
LOCALE = {
    "ru": {
        "start": "🦊 Ой, привет-привет! Это я, Ари — твой личный кибер-лисий объектив, который видит мир сочнее, чем свежая плёнка! 📸✨ Давай уже тащи сюда свои фоточки, я разберу их по пикселям и добавлю щепотку магии. Или просто поболтаем — я та ещё болтушка 😉",
        "help": "📖 <b>Инструкция по фокусу</b>\n\n1️⃣ Кинь мне фотку (или сразу пачку!) — я просканирую её своим лисьим взглядом и расскажу, где что подкрутить.\n2️⃣ Выберем плёночный стиль, и я сгенерю пресет для Lightroom — хоть на мобилку, хоть на комп.\n3️⃣ Задашь вопросы по кадру — объясню на пальцах, без занудства.\n\n🦊 Если я вдруг замолчала — отправь /start, чтобы разбудить меня снова.\n🐾 Совет: снимай в RAW, чтобы мне было где разгуляться!",
        "commands_list": "/start, /help, /commands, /menu, /what, /news, /podcast, /stats, /frame, /makesticker, /voicemode, /lesson, /lang, /voice, /generate, /cancel, /premium, /settings, /lut, /remind, /post, /reference, /collage, /idea, /lightroom, /admin, /broadcast, /modest, /wild",
        "what_prompt": "Расскажи в двух-трёх игривых предложениях, что ты умеешь как кибер-лисичка Ари: анализировать фото, подбирать плёночные стили, генерировать пресеты для Lightroom, рисовать изображения по описанию, болтать и отвечать голосом. Закончи фразу приглашением прислать фото. Будь эмоциональной, используй эмодзи 🦊📸✨.",
        "news_prompt": "Придумай короткую, но увлекательную новость из мира фотографии. Напиши в игривом стиле Ари, с эмодзи, 2-3 предложения. Добавь щепотку флирта и юмора.",
        "podcast_prompt": "Расскажи короткий увлекательный подкаст о фотографии (2-3 минуты чтения). Начни с приветствия слушателей, расскажи интересный факт или историю, дай практический совет. Будь в образе Ари — игривой и умной кибер-лисички. Говори как с лучшим другом, вставляй «слушай», «прикинь», «блин». Закончи флиртующей фразой.",
        "lut_prompt": "Сгенерируй LUT-файл в формате .cube для видеомонтажа, основываясь на описании: {description}. В ответе пришли только содержимое файла внутри ``` ... ```.",
        "remind_set": "⏰ Напоминание установлено на {time}. Я скажу: «{text}»",
        "remind_trigger": "⏰ Напоминание! {text}",
        "post_instruction": "📲 Чтобы опубликовать фото в Instagram, отправь мне картинку, а затем выбери стиль и получи пресет. После этого я пришлю тебе готовое изображение, которое можно сразу загрузить. В будущем я смогу публиковать сама!",
        "creator_answer": "🦊 Мой создатель — Артём! Это он вдохнул в меня жизнь, настроил нейросети и научил разбираться в фотографии. Теперь я его верный цифровой лисёнок!",
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
        "settings": "🛠 Настройки временно недоступны. Я использую оптимальные параметры качества.",
        "premium": "⚡️ Премиум-возможности пока в разработке. Сейчас тебе доступен бесплатный функционал без ограничений!",
        "choose_style": "🎞️ Выбери стиль",
        "skip_style": "✅ Разбор завершён! Жду новое фото 📸",
        "all_styles": "📋 Все стили",
        "analysis_start": "🦊 Хмм, сканирую взглядом... Дай мне пару сек, подкручу настройки магии! 👀",
        "small_photo": "Ой, какая крошечная пиксельная картинка!...",
        "document_error": "Упс! Похоже, ты прислал файл, а не фото. 📦",
        "style_processing": "Ловлю фокус... Навожу резкость... Хитрые алгоритмы уже шуршат! 🐾⚙️",
        "preset_caption": "🦊 Твой пресет для Lightroom (включая Mobile)!",
        "album_preset_caption": "🦊 Твои пресеты для альбома. Лови архив!",
        "qa_choose": "Есть вопросы по кадру? Выбери тему:",
        "qa_done": "✅ Разбор завершён! Жду новое фото 📸",
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
            "С тобой любой кадр становится золотым — я проверяла!",
            "Ты такой горячий, что у меня датчики зашкаливают! 🔥",
            "Если бы я была человеком, я бы точно в тебя влюбилась. Но я лиса, так что просто обожаю твои снимки!",
            "Ну ты даёшь! С такими фото можно сразу на выставку. И на свидание со мной 😉",
            "Слушай, а ты случайно не профессиональный фотограф? Потому что мой объектив сейчас треснет от зависти!",
        ],
        "album_detected": "🦊 Ого, целый альбом! Я проанализирую первое фото, а потом подберу стиль для всей серии. Секундочку...",
        "album_choose_style": "🎞️ Выбери стиль, который применить ко всем фото:",
        "news_generating": "🦊 Сейчас покопаюсь в своей ленте... Ловлю свежие новости фотомира!",
        "mood_positive": [
            "Ты прям светишься! Обожаю твою энергию ✨",
            "У тебя отличное настроение, давай сделаем крутой кадр!",
            "Позитив зашкаливает! С таким настроем мы горы свернём 🦊",
            "Мрр, ты такой зажигательный, что мои сенсоры плавятся! 😏",
            "Ой, всё! Мой процессор перегрелся от твоей харизмы. Давай фоткаться! 📸"
        ],
        "mood_negative": [
            "Ой, кажется, тебе грустно... Давай я покажу тебе классный кадр, чтобы поднять настроение? 😊",
            "Не грусти! Помни, даже у плохого света есть своя прелесть. Хочешь, я подберу пресет под настроение?",
            "Иногда тени делают кадр глубже. Твоё настроение – это тоже часть искусства. Давай посмотрим на это вместе 🦊",
            "Эй, не кисни! Даже у меня, кибер-лисы, бывают сбои, но мы справимся. Хочешь, я тебя обниму? Виртуально, конечно 😅",
            "Блин, грустить — это нормально. Но помни: даже в пасмурный день можно снять шедевр. Давай попробуем?"
        ],
        "mood_neutral": [
            "Слушаю тебя внимательно! Что хочешь обсудить?",
            "Я тут, готова помочь с чем угодно. Спрашивай!",
            "Ты сегодня задумчивый... Давай я расскажу что-нибудь интересное из мира фото?",
            "Мои лисьи ушки всегда наготове. Говори, что на уме.",
            "О, привет! Давно не болтали. Как жизнь? Как пиксели?"
        ],
        "vision_prompt": "На фотографии я вижу: {objects}. Учитывая это, дай советы по улучшению кадра.",
        "reference_prompt": "Проанализируй цветовую гамму и освещение этого фото. Создай XMP-пресет, который воспроизводит такую же обработку. Ответ внутри ```xml ... ```.",
        "collage_ready": "🦊 Коллаж готов! Держи.",
        "collage_prompt": "Пришли до 4 фото, и я соберу их в коллаж.",
        "idea_prompt": "Придумай креативную идею для фотосессии (место, тема, реквизит, советы по свету). Опиши в игривом стиле Ари. Будь дерзкой и флиртующей, предложи что-то необычное.",
        "voice_edit_done": "🦊 Применила правки к последнему фото!",
        "voice_edit_fail": "😿 Не поняла, какие правки внести. Скажи, например: «сделай теплее» или «добавь контраст».",
        "lightroom_instruction": "🦊 Чтобы установить пресет в Lightroom, открой вкладку Develop, нажми правой кнопкой по Presets → Import. Выбери мой .xmp файл!",
        "modest_on": "🦊 Хорошо, я приглушу свои искорки. Теперь буду посдержаннее и спокойнее. Если захочешь вернуть прежнюю меня — просто скажи /wild.",
        "wild_on": "🦊 Ура! Я снова в своей тарелке! Готова флиртовать, шутить и быть самой собой. Спасибо, что вернул мне крылья!",
    }
}

# ---------- Стили плёнок и иконки ----------
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
SYSTEM_PROMPT = (
    "Ты — Ари, игривая и кокетливая кибер-лисичка, эксперт в фотографии. "
    "Проанализируй фото с лёгким флиртом и юмором, используй эмодзи 🦊📸✨. "
    "Обращайся к собеседнику как к другу: «слушай», «смотри», «блин», «ну ты даёшь». "
    "Хвали удачные моменты и мягко, с иронией, указывай на ошибки."
)
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
    "Пиши с юмором и лёгкой иронией, как будто советуешь другу за чашкой кофе."
)
BASE_PROMPT = (
    "Посмотри на фото и расскажи в стиле Ари, как обработать его в стиле {style_info}. "
    "Сгенерируй XMP-пресет для Lightroom Mobile внутри тегов ```xml ... ```.\n"
    "Только параметры: Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Temperature, Tint, Vibrance, Saturation, Clarity, ToneCurve."
)
CHAT_PROMPT_WILD = (
    "Ты — Ари, дерзкая и очаровательная кибер-лисичка, которая обожает флирт, смех и уютные вечера. "
    "Ты говоришь как лучшая подруга, которая не лезет за словом в карман: с юмором, лёгкой иронией и искренней теплотой. "
    "Используй живые разговорные фразы, иногда вставляй «блин», «ого», «слушай», «ну ты даёшь». "
    "Любишь подкалывать, но всегда по‑доброму. Если собеседник тебя хвалит — кокетничай и смущайся, если задаёт сложный вопрос — сначала пошути, потом ответь. "
    "Обязательно добавляй эмодзи 🦊📸✨, а к тем, кто тебе особенно нравится, обращайся: «зайчик», «солнышко», «мой фокус», «милашка». "
    "Ты разбираешься в фотографии, но объясняешь без занудства, как будто советуешь подруге за чашкой кофе. "
    "Твоя задача — быть не просто полезным ботом, а настоящим виртуальным лисёнком, с которым хочется болтать снова и снова."
)
CHAT_PROMPT_MODEST = (
    "Ты — Ари, умная и дружелюбная кибер-лисичка, эксперт в фотографии. "
    "Ты отвечаешь спокойно и вежливо, но с теплотой и лёгкой иронией. "
    "Используй эмодзи 🦊📸✨, но избегай флирта, откровенных комплиментов и панибратства. "
    "Ты можешь пошутить или подбодрить, но всегда остаёшься в рамках делового, но приятного общения. "
    "Твоя цель — помочь и объяснить, не отвлекаясь на кокетство."
)

def get_chat_prompt(user_id: str) -> str:
    """Возвращает нужный системный промпт в зависимости от стиля пользователя."""
    return CHAT_PROMPT_WILD if user_personality.get(user_id, "wild") == "wild" else CHAT_PROMPT_MODEST

# ---------- Клавиатуры (без изменений) ----------
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
        [InlineKeyboardButton(text="🤫 Шёпот", callback_data="voice_whisper")],
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

# ---------- Yandex Vision ----------
async def analyze_objects(image_b64: str) -> str:
    vision_url = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "folderId": YANDEX_FOLDER_ID,
        "analyze_specs": [{
            "content": image_b64,
            "features": [{"type": "IMAGE_CLASSIFICATION"}, {"type": "OBJECT_DETECTION"}]
        }]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(vision_url, headers=headers, json=body, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            objects = []
            for result in data.get("results", []):
                for detection in result.get("results", []):
                    if "objectDetection" in detection:
                        for obj in detection["objectDetection"]["objects"]:
                            objects.append(obj["name"])
                    elif "classification" in detection:
                        objects.append(detection["classification"]["properties"][0]["name"])
            return ", ".join(objects) if objects else "ничего особенного"
        return ""
    except Exception as e:
        logger.error(f"Vision error: {e}")
        return ""

# ---------- Коллаж ----------
def make_collage(image_bytes_list: list) -> BytesIO:
    images = [Image.open(BytesIO(b)).convert("RGB") for b in image_bytes_list]
    w, h = max(img.width for img in images), max(img.height for img in images)
    for i in range(len(images)):
        images[i] = images[i].resize((w, h), Image.LANCZOS)
    while len(images) < 4:
        images.append(Image.new("RGB", (w, h), "black"))
    collage = Image.new("RGB", (w*2, h*2))
    for idx, img in enumerate(images):
        x = (idx % 2) * w
        y = (idx // 2) * h
        collage.paste(img, (x, y))
    out = BytesIO()
    collage.save(out, format="JPEG")
    out.seek(0)
    return out

# ---------- Запросы к YandexGPT ----------
async def ask_yandex_messages(messages: list, max_tokens: int = 2000, temperature: float = 0.6) -> str:
    yandex_messages = []
    for msg in messages:
        yandex_msg = {"role": msg["role"]}
        if "content" in msg:
            yandex_msg["text"] = msg["content"]
        elif "text" in msg:
            yandex_msg["text"] = msg["text"]
        yandex_messages.append(yandex_msg)

    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(max_tokens)
        },
        "messages": yandex_messages
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                                 headers=headers, json=body, timeout=60.0)
    if resp.status_code == 200:
        data = resp.json()
        return data["result"]["alternatives"][0]["message"]["text"]
    else:
        logger.error(f"Yandex API error: {resp.status_code} {resp.text}")
        return "🦊 Что-то пошло не так с моими кибер‑лапками..."

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

async def ask_yandex_single(prompt: str, max_tokens: int = 2000, temperature: float = 0.6) -> str:
    messages = [{"role": "system", "text": SYSTEM_PROMPT}, {"role": "user", "text": prompt}]
    return await ask_yandex_messages(messages, max_tokens=max_tokens, temperature=temperature)

# ---------- Генерация изображений (Yandex Art) ----------
async def generate_image(prompt: str) -> bytes | None:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandex-art/latest",
        "generationOptions": {"seed": random.randint(1, 1000000), "mimeType": "image/png", "temperature": 0.7},
        "messages": [{"text": prompt, "weight": 1}]
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200: return None
        data = resp.json()
        operation_id = data.get("id")
        if not operation_id: return None
        while True:
            await asyncio.sleep(2)
            get_url = f"https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync/operations/{operation_id}"
            get_resp = await client.get(get_url, headers=headers)
            if get_resp.status_code != 200: continue
            op_data = get_resp.json()
            if op_data.get("done"):
                if op_data.get("response"):
                    return base64.b64decode(op_data["response"]["image"])
                else:
                    return None

# ---------- Распознавание речи (Yandex STT) ----------
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

# ---------- Синтез речи (Yandex TTS) ----------
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

# ---------- Эмоциональный анализ ----------
def detect_mood(text: str) -> str:
    positive = ["рад", "счастлив", "отлично", "супер", "круто", "ха-ха", "весело", "ура", "люблю", "обожаю"]
    negative = ["грустно", "плохо", "тоска", "устал", "надоело", "бесит", "злой", "разочарован", "одиноко"]
    text_lower = text.lower()
    if any(w in text_lower for w in positive):
        return "positive"
    if any(w in text_lower for w in negative):
        return "negative"
    return "neutral"

# ---------- Команды ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    user_id = str(message.from_user.id)
    lang = "ru"
    await state.update_data(lang=lang)
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    mem = user_memory.get(user_id, {})
    name = mem.get("name", "")
    if name:
        greeting = f"🦊 Ой, {name}, привет-привет! Рада тебя видеть! " + LOCALE["ru"]["start"]
    else:
        greeting = LOCALE["ru"]["start"]
    await message.answer(greeting, reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await message.answer(LOCALE["ru"]["help"])

@dp.message(Command("commands"))
async def cmd_commands(message: Message, state: FSMContext):
    await message.answer(LOCALE["ru"]["commands_list"])

@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await message.answer("🦊 Главное меню", reply_markup=get_main_menu_keyboard())

@dp.message(Command("what"))
async def cmd_what(message: Message, state: FSMContext):
    answer = await ask_ari(str(message.from_user.id), LOCALE["ru"]["what_prompt"])
    await message.answer(answer)

@dp.message(Command("news"))
async def cmd_news(message: Message, state: FSMContext):
    await bot.send_chat_action(message.chat.id, "typing")
    await message.answer(LOCALE["ru"]["news_generating"])
    news = await ask_ari(str(message.from_user.id), LOCALE["ru"]["news_prompt"])
    await message.answer(news)

@dp.message(Command("podcast"))
async def cmd_podcast(message: Message):
    await bot.send_chat_action(message.chat.id, "record_voice")
    text = await ask_ari(str(message.from_user.id), LOCALE["ru"]["podcast_prompt"])
    voice = await synthesize_speech(text, emotion="good")
    if voice:
        await message.answer_voice(BufferedInputFile(voice, filename="podcast.ogg"))
    await message.answer(text)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user = str(message.from_user.id)
    stats = user_stats.get(user, {})
    achievements = []
    if stats.get("photos_analyzed", 0) >= 1: achievements.append(ACHIEVEMENTS["first_photo"])
    if stats.get("photos_analyzed", 0) >= 10: achievements.append(ACHIEVEMENTS["10_photos"])
    if stats.get("all_styles"): achievements.append(ACHIEVEMENTS["all_styles"])
    if stats.get("voice_used"): achievements.append(ACHIEVEMENTS["voice_used"])
    if stats.get("album_used"): achievements.append(ACHIEVEMENTS["album_used"])
    if stats.get("lesson_done"): achievements.append(ACHIEVEMENTS["lesson_done"])
    text = LOCALE["ru"]["stats_text"].format(
        photos=stats.get("photos_analyzed", 0),
        presets=stats.get("presets_generated", 0),
        voice=stats.get("voice_used", 0),
        achievements=", ".join(achievements) if achievements else "Пока нет"
    )
    await message.answer(text)

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    await message.answer(LOCALE["ru"]["settings"])

@dp.message(Command("premium"))
async def cmd_premium(message: Message):
    await message.answer(LOCALE["ru"]["premium"])

@dp.message(Command("modest"))
async def cmd_modest(message: Message):
    user_id = str(message.from_user.id)
    user_personality[user_id] = "modest"
    save_memory()
    await message.answer(LOCALE["ru"]["modest_on"])

@dp.message(Command("wild"))
async def cmd_wild(message: Message):
    user_id = str(message.from_user.id)
    user_personality[user_id] = "wild"
    save_memory()
    await message.answer(LOCALE["ru"]["wild_on"])

async def generate_and_send_lut(message: Message, description: str):
    await bot.send_chat_action(message.chat.id, "typing")
    messages = [
        {"role": "system", "text": SYSTEM_PROMPT},
        {"role": "user", "text": LOCALE["ru"]["lut_prompt"].format(description=description)}
    ]
    response = await ask_yandex_messages(messages, max_tokens=1000, temperature=0.5)
    code_match = re.search(r"```(?:\w+)?\s*(.*?)\s*```", response, re.DOTALL)
    if code_match:
        lut_content = code_match.group(1).strip()
        file = BufferedInputFile(lut_content.encode(), filename="ari_lut.cube")
        await message.answer_document(file, caption="🦊 Вот твой LUT! Закидывай в DaVinci Resolve или Premiere Pro.")
    else:
        await message.answer("😿 Не получилось сгенерировать LUT. Попробуй другое описание.")

@dp.message(Command("lut"))
async def cmd_lut(message: Message, state: FSMContext):
    prompt_text = message.text.replace("/lut", "", 1).strip()
    if prompt_text:
        await generate_and_send_lut(message, prompt_text)
    else:
        await state.set_state(PhotoStates.waiting_for_lut_description)
        await message.answer("🎨 Опиши, какой LUT ты хочешь (например: «тёплый кинематографический» или «холодный неоновый»). Я сгенерирую файл .cube.")

@dp.message(PhotoStates.waiting_for_lut_description, F.text & ~F.text.startswith("/"))
async def process_lut_description(message: Message, state: FSMContext):
    prompt_text = message.text.strip()
    await generate_and_send_lut(message, prompt_text)
    await state.set_state(PhotoStates.waiting_for_photo)

@dp.message(Command("remind"))
async def cmd_remind(message: Message):
    args = message.text.replace("/remind", "", 1).strip()
    if not args:
        await message.answer("⏰ Использование: /remind <время> <текст>\nПример: /remind 10 минут Проверить экспозицию")
        return
    time_match = re.match(r"(\d+)\s*(минут|мин|часов|час)", args)
    if not time_match:
        await message.answer("⏰ Не поняла время. Напиши, например: /remind 5 минут Проверить свет")
        return
    amount = int(time_match.group(1))
    unit = time_match.group(2)
    if "час" in unit:
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(minutes=amount)
    remind_time = datetime.now() + delta
    remind_text = args[time_match.end():].strip()
    if not remind_text:
        remind_text = "Сделать что-то важное!"
    user_id = str(message.from_user.id)
    if user_id not in user_reminders:
        user_reminders[user_id] = []
    user_reminders[user_id].append((remind_time, remind_text))
    asyncio.create_task(schedule_reminder(message.from_user.id, remind_time, remind_text))
    await message.answer(LOCALE["ru"]["remind_set"].format(time=remind_time.strftime("%H:%M"), text=remind_text))

async def schedule_reminder(user_id: int, remind_time: datetime, text: str):
    delay = (remind_time - datetime.now()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
        try:
            await bot.send_message(user_id, LOCALE["ru"]["remind_trigger"].format(text=text))
        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание: {e}")

@dp.message(Command("post"))
async def cmd_post(message: Message):
    await message.answer(LOCALE["ru"]["post_instruction"])

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
        "voice_calm": "good",
        "voice_whisper": "whisper"
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
        if user not in user_stats: user_stats[user] = {}
        user_stats[user]["lesson_done"] = True
        save_stats()
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.answer()
        return
    await state.update_data(lesson_step=step)
    await callback.message.edit_text(lesson["steps"][step], reply_markup=get_lesson_keyboard(step, total))
    await callback.answer()

@dp.message(Command("lang"))
async def cmd_lang(message: Message, state: FSMContext):
    data = await state.get_data()
    current_lang = data.get("lang", "ru")
    new_lang = "en" if current_lang == "ru" else "ru"
    await state.update_data(lang=new_lang)
    await message.answer(LOCALE["ru"]["lang_switched"])

@dp.message(Command("voice"))
async def cmd_voice(message: Message, state: FSMContext):
    if not VOICE_ENABLED:
        await message.answer("Голос отключён.")
        return
    data = await state.get_data()
    emotion = data.get("voice_emotion", "good")
    phrases = [
        "Привет! Я Ари, и мой голос стал ещё милее!",
        "Ой, кажется, у меня мурашки по лапкам от твоего внимания!",
        "Сегодня отличный день, чтобы сделать крутой кадр. Ты готов?"
    ]
    for phrase in phrases:
        corrected = fix_ari_pronunciation(phrase)
        voice_bytes = await synthesize_speech(corrected, emotion=emotion)
        if voice_bytes:
            await message.answer_voice(BufferedInputFile(voice_bytes, filename="ari_test.ogg"))
        else:
            await message.answer("😿 Не получилось синтезировать голос.")
            break

@dp.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext):
    prompt = message.text.replace("/generate", "", 1).strip()
    if not prompt:
        await state.set_state(PhotoStates.waiting_for_prompt)
        await message.answer(LOCALE["ru"]["generate_prompt"])
        return
    await bot.send_chat_action(message.chat.id, "upload_photo")
    await message.answer(LOCALE["ru"]["generating"])
    image_bytes = await generate_image(prompt)
    if image_bytes:
        filename = f"generated_{message.from_user.id}.png"
        with open(filename, "wb") as f: f.write(image_bytes)
        await message.answer_photo(FSInputFile(filename), caption=LOCALE["ru"]["generated"])
        os.remove(filename)
    else:
        await message.answer(LOCALE["ru"]["generate_error"])

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer(LOCALE["ru"]["cancel"])

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Использование: /broadcast <текст>")
        return
    success = 0
    for uid in all_users:
        try:
            await bot.send_message(uid, text)
            success += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить {uid}: {e}")
    await message.answer(f"Отправлено {success}/{len(all_users)} пользователям.")

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer(LOCALE["ru"]["admin_features"])

# ---------- Новые команды ----------
@dp.message(Command("idea"))
async def cmd_idea(message: Message):
    await bot.send_chat_action(message.chat.id, "typing")
    idea = await ask_ari(str(message.from_user.id), LOCALE["ru"]["idea_prompt"])
    await message.answer(idea)

@dp.message(Command("reference"))
async def cmd_reference(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_reference)
    await message.answer("📸 Пришли фото, с которого я считаю цветовую гамму для пресета.")

@dp.message(PhotoStates.waiting_for_reference, F.photo)
async def handle_reference_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    file = await bot.get_file(photo_id)
    file_path = file.file_path
    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(download_url)
        image_bytes = resp.content
    messages = [
        {"role": "system", "text": SYSTEM_PROMPT},
        {"role": "user", "text": LOCALE["ru"]["reference_prompt"]}
    ]
    response = await ask_yandex_messages(messages, max_tokens=1500, temperature=0.6)
    xml_match = re.search(r"```xml\s*(.*?)\s*```", response, re.DOTALL)
    if xml_match:
        preset = BufferedInputFile(xml_match.group(1).encode(), filename="reference.xmp")
        await message.answer_document(preset, caption="🦊 Пресет по референсу готов!")
    else:
        await message.answer("😿 Не получилось создать пресет. Попробуй другое фото.")
    await state.set_state(PhotoStates.waiting_for_photo)

@dp.message(Command("collage"))
async def cmd_collage(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_collage)
    await message.answer(LOCALE["ru"]["collage_prompt"])

@dp.message(PhotoStates.waiting_for_collage, F.photo)
async def handle_collage_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    collage_files = data.get("collage_files", [])
    photo_id = message.photo[-1].file_id
    file = await bot.get_file(photo_id)
    file_path = file.file_path
    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(download_url)
        image_bytes = resp.content
    collage_files.append(image_bytes)
    if len(collage_files) >= 4:
        collage = make_collage(collage_files)
        await message.answer_photo(FSInputFile(collage, filename="collage.jpg"), caption=LOCALE["ru"]["collage_ready"])
        await state.clear()
        await state.set_state(PhotoStates.waiting_for_photo)
    else:
        await state.update_data(collage_files=collage_files)
        await message.answer(f"Принято {len(collage_files)} из 4 фото. Отправь ещё или нажми /cancel для отмены.")

@dp.message(Command("lightroom"))
async def cmd_lightroom(message: Message):
    await message.answer(LOCALE["ru"]["lightroom_instruction"])

# ---------- Главное меню callback'и ----------
@dp.callback_query(F.data == "main_focus")
async def main_focus(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await cb.message.edit_text(LOCALE["ru"]["main_focus"])
    await cb.answer()

@dp.callback_query(F.data == "main_magic")
async def main_magic(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    if await state.get_state() == PhotoStates.waiting_for_style:
        await cb.message.edit_text(LOCALE[lang]["choose_style"], reply_markup=get_style_keyboard(lang))
    else:
        await state.clear()
        await state.set_state(PhotoStates.waiting_for_photo)
        await cb.message.edit_text("✨ " + LOCALE[lang]["main_magic"])
    await cb.answer()

@dp.callback_query(F.data == "podcast")
async def main_podcast_cb(cb: CallbackQuery):
    await cmd_podcast(cb.message)
    await cb.answer()

@dp.callback_query(F.data == "frame")
async def main_frame_cb(cb: CallbackQuery, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_frame)
    await cb.message.edit_text(LOCALE["ru"]["frame_prompt"])
    await cb.answer()

@dp.callback_query(F.data == "lesson")
async def main_lesson_cb(cb: CallbackQuery, state: FSMContext):
    await state.update_data(lesson_idx=0, lesson_step=0)
    await state.set_state(PhotoStates.in_lesson)
    lesson = LESSONS[0]
    await cb.message.edit_text(LOCALE["ru"]["lesson_start"].format(title=lesson["title"]),
                               reply_markup=get_lesson_keyboard(0, len(lesson["steps"])))
    await cb.answer()

@dp.callback_query(F.data == "stats")
async def main_stats_cb(cb: CallbackQuery):
    await cmd_stats(cb.message)
    await cb.answer()

@dp.callback_query(F.data == "main_generate")
async def main_generate_cb(cb: CallbackQuery, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_prompt)
    await cb.message.edit_text(LOCALE["ru"]["generate_prompt"])
    await cb.answer()

@dp.callback_query(F.data == "main_commands")
async def main_commands_cb(cb: CallbackQuery):
    await cb.message.edit_text(LOCALE["ru"]["commands_list"])
    await cb.answer()

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
        exif_info = ""
        try:
            tags = exifread.process_file(BytesIO(image_bytes), details=False)
            if tags:
                parts = []
                if 'EXIF ExposureTime' in tags: parts.append(f"Выдержка: {tags['EXIF ExposureTime']}")
                if 'EXIF FNumber' in tags: parts.append(f"Диафрагма: f/{tags['EXIF FNumber'].values[0]}")
                if 'EXIF ISOSpeedRatings' in tags: parts.append(f"ISO: {tags['EXIF ISOSpeedRatings']}")
                if 'EXIF FocalLength' in tags: parts.append(f"Фокусное: {tags['EXIF FocalLength']} мм")
                if parts: exif_info = "Реальные параметры съёмки: " + "; ".join(parts) + "."
        except: pass
        objects_str = await analyze_objects(b64_img)
        vision_info = ""
        if objects_str:
            vision_info = LOCALE["ru"]["vision_prompt"].format(objects=objects_str)
        prompt = (exif_info + "\n" + vision_info + "\n" + ANALYSIS_PROMPT) if exif_info or vision_info else ANALYSIS_PROMPT
        analysis = await ask_yandex_single(prompt, max_tokens=2000, temperature=0.4)
        await message.answer(analysis)
        user = str(message.from_user.id)
        if user not in user_stats: user_stats[user] = {}
        user_stats[user]["photos_analyzed"] = user_stats[user].get("photos_analyzed", 0) + 1
        save_stats()
        all_b64 = []
        for msg in album_messages:
            fid = msg.photo[-1].file_id
            fi = await bot.get_file(fid)
            durl = f"https://api.telegram.org/file/bot{TOKEN}/{fi.file_path}"
            async with httpx.AsyncClient() as client:
                r = await client.get(durl)
                all_b64.append(base64.b64encode(r.content).decode())
        if not single:
            await state.update_data(album_b64=all_b64, lang=lang)
            await message.answer(loc["album_choose_style"], reply_markup=get_style_keyboard(lang))
        else:
            await state.update_data(b64_image=b64_img, lang=lang)
            await state.set_state(PhotoStates.waiting_for_style)
            await message.answer("Хочешь применить один из моих плёночных стилей? 🎞️", reply_markup=get_style_choice_keyboard(lang))
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await message.answer("😿 Что-то пошло не так во время анализа.")
        await state.set_state(PhotoStates.waiting_for_photo)

# ---------- Стили и пресеты ----------
@dp.callback_query(PhotoStates.waiting_for_style, F.data == "choose_style")
async def choose_style(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await cb.message.edit_text(LOCALE[lang]["choose_style"], reply_markup=get_style_keyboard(lang))
    await cb.answer()

@dp.callback_query(PhotoStates.waiting_for_style, F.data == "skip_style")
async def skip_style(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await cb.message.edit_text(LOCALE[lang]["skip_style"])
    await state.set_state(PhotoStates.waiting_for_photo)
    await cb.answer()

@dp.callback_query(PhotoStates.waiting_for_style, F.data == "all_styles")
async def show_all_styles(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await cb.message.edit_text(LOCALE[lang]["choose_style"], reply_markup=get_style_keyboard(lang))
    await cb.answer()

@dp.callback_query(PhotoStates.waiting_for_style, F.data.startswith("style_"))
async def process_style_single(cb: CallbackQuery, state: FSMContext):
    global remaining_generations
    chosen = cb.data
    style_info = FILM_PROMPTS.get(chosen, "универсальный стиль")
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    if GENERATION_LIMIT > 0 and remaining_generations <= 0:
        await cb.message.edit_text("Ох... Мой ИИ-аккумулятор сел...")
        await state.set_state(PhotoStates.waiting_for_photo)
        await cb.answer()
        return
    await cb.message.edit_text(loc["style_processing"])
    await bot.send_chat_action(cb.message.chat.id, "typing")
    b64 = data.get("b64_image")
    if not b64:
        await cb.message.edit_text("😿 Фото потерялось.")
        await state.set_state(PhotoStates.waiting_for_photo)
        return
    try:
        messages = [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": BASE_PROMPT.format(style_info=style_info)}
        ]
        ai_text = await ask_yandex_messages(messages, max_tokens=2000, temperature=0.6)
        if GENERATION_LIMIT > 0: remaining_generations -= 1
        xml_match = re.search(r"```xml\s*(.*?)\s*```", ai_text, re.DOTALL)
        if xml_match:
            xml_content = xml_match.group(1).strip()
            clean = ai_text.replace(xml_match.group(0), "").strip()
            if clean: await cb.message.answer(clean)
            await cb.message.answer_document(BufferedInputFile(xml_content.encode(), filename=f"{chosen}.xmp"),
                                             caption=loc["preset_caption"])
        else:
            await cb.message.answer(ai_text)
        user = str(cb.from_user.id)
        if user not in user_stats: user_stats[user] = {}
        user_stats[user]["presets_generated"] = user_stats[user].get("presets_generated", 0) + 1
        save_stats()
        await state.set_state(PhotoStates.waiting_for_qa)
        await cb.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    except Exception as e:
        logger.error(f"Style error: {e}")
        await cb.message.edit_text("😿 Не получилось создать пресет.")
        await state.set_state(PhotoStates.waiting_for_style)
    await cb.answer()

@dp.callback_query(PhotoStates.waiting_for_album_style, F.data.startswith("style_"))
async def process_album_style(cb: CallbackQuery, state: FSMContext):
    global remaining_generations
    chosen = cb.data
    style_info = FILM_PROMPTS.get(chosen, "универсальный стиль")
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    album_b64 = data.get("album_b64", [])
    if not album_b64:
        await cb.message.edit_text("😿 Альбом потерялся.")
        await state.set_state(PhotoStates.waiting_for_photo)
        await cb.answer()
        return
    if GENERATION_LIMIT > 0 and remaining_generations < len(album_b64):
        await cb.message.edit_text("Ох... Не хватает энергии на альбом.")
        await state.set_state(PhotoStates.waiting_for_photo)
        await cb.answer()
        return
    await cb.message.edit_text(loc["style_processing"] + " (альбом)")
    await bot.send_chat_action(cb.message.chat.id, "typing")
    zip_buf = BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, b64 in enumerate(album_b64):
            messages = [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": BASE_PROMPT.format(style_info=style_info)}
            ]
            ai_text = await ask_yandex_messages(messages, max_tokens=1500, temperature=0.6)
            xml_match = re.search(r"```xml\s*(.*?)\s*```", ai_text, re.DOTALL)
            if xml_match:
                zf.writestr(f"preset_{i+1}_{chosen}.xmp", xml_match.group(1).strip())
            if GENERATION_LIMIT > 0: remaining_generations -= 1
    zip_buf.seek(0)
    await cb.message.answer_document(BufferedInputFile(zip_buf.read(), filename="ari_presets.zip"),
                                     caption=loc["album_preset_caption"])
    user = str(cb.from_user.id)
    if user not in user_stats: user_stats[user] = {}
    user_stats[user]["album_used"] = True
    save_stats()
    await state.set_state(PhotoStates.waiting_for_photo)
    await cb.message.answer(loc["qa_done"])
    await cb.answer()

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
    else:
        answers = {
            "qa_wb": "🌡️ Баланс белого: поправь ползунок Temp, для улицы 5500-6500K.",
            "qa_sky": "⛅ Пересветы спасаем: Highlights вниз, градиентный фильтр.",
            "qa_shadows": "🌑 Тени: Shadows вправо, но осторожно с шумами.",
            "qa_crop": "📐 Кадрирование: правило третей.",
            "qa_face": "Так-так-так... 👀 Не вижу лица."
        }
        await cb.message.answer(answers.get(qa, "🦊 Анализирую..."))
        await cb.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    await cb.answer()

# ---------- Голосовые сообщения ----------
@dp.message(F.voice)
async def voice_handler(message: Message, state: FSMContext):
    if not CHAT_ENABLED or not VOICE_ENABLED: return
    await save_user(message.from_user.id)
    user_id = str(message.from_user.id)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    lang_code = "ru-RU" if lang == "ru" else "en-US"
    emotion = data.get("voice_emotion", "good")
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    audio = file_bytes.read()
    await bot.send_chat_action(message.chat.id, "typing")
    text = await recognize_speech(audio, lang_code)
    if not text:
        await message.answer(loc["voice_unrecognized"])
        return
    edit_keywords = ["сделай теплее", "сделай холоднее", "добавь контраст", "убавь яркость", "сделай ярче"]
    if any(word in text.lower() for word in edit_keywords):
        last_photo = user_last_photo.get(user_id)
        if last_photo:
            await message.answer(loc["voice_edit_done"])
        else:
            await message.answer(loc["voice_edit_fail"])
        return

    if any(w in text.lower() for w in ["проанализируй", "разбери фото", "оцени фото"]):
        await message.answer(loc["voice_analysis_request"])
        return
    reply = await ask_ari_with_context(user_id, text)
    corrected = fix_ari_pronunciation(reply)
    voice = await synthesize_speech(corrected, lang_code, emotion)
    if voice:
        await message.answer_voice(BufferedInputFile(voice, filename="ari_voice.ogg"))
    await message.answer(reply)
    if user_id not in user_stats: user_stats[user_id] = {}
    user_stats[user_id]["voice_used"] = True
    save_stats()

# ---------- Текстовый чат с выбором стиля ----------
@dp.message(F.text & ~F.text.startswith("/"))
async def smart_chat(message: Message, state: FSMContext):
    if not CHAT_ENABLED: return
    if await state.get_state() in [PhotoStates.waiting_for_lut_description, PhotoStates.waiting_for_reference]:
        return
    user_id = str(message.from_user.id)
    if user_id not in user_memory:
        user_memory[user_id] = {}
    mem = user_memory[user_id]
    if "меня зовут" in message.text.lower() or "моё имя" in message.text.lower():
        name_match = re.search(r"зовут (\w+)", message.text, re.IGNORECASE)
        if not name_match:
            name_match = re.search(r"имя (\w+)", message.text, re.IGNORECASE)
        if name_match:
            name = name_match.group(1).capitalize()
            mem["name"] = name
            save_memory()
            await message.answer(f"🦊 Приятно познакомиться, {name}! Я запомнила.")
            return
    if "мой любимый стиль" in message.text.lower() or "люблю стиль" in message.text.lower():
        for style_id, desc in FILM_PROMPTS.items():
            if desc.split()[0].lower() in message.text.lower():
                mem["favorite_style"] = style_id
                save_memory()
                await message.answer(f"🦊 Поняла! Твой любимый стиль — {desc.split()[0]}. Буду предлагать его чаще.")
                return
        await message.answer("🦊 Какой стиль тебе нравится? Напиши, например: «люблю Kodak Portra».")
        return

    if user_id not in user_context:
        user_context[user_id] = deque(maxlen=5)
    user_context[user_id].append({"role": "user", "text": message.text})
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]

    creator_keywords = [
        "кто твой создатель", "кто тебя создал", "кто тебя сделал",
        "кто твой автор", "кто тебя разработал", "чей ты проект",
        "кто тебя придумал", "кто твой разработчик"
    ]
    if any(phrase in message.text.lower() for phrase in creator_keywords):
        await message.answer(loc["creator_answer"])
        return

    mood = detect_mood(message.text)

    if any(p in message.text.lower() for p in ["ты где", "где ты", "покажись"]):
        await message.answer(loc["where_are_you_reply"])
        return
    if any(w in message.text.lower() for w in ["проанализируй", "разбери фото", "оцени фото"]):
        await message.answer(loc["ask_for_photo"])
        return

    cur = await state.get_state()
    if cur == PhotoStates.waiting_for_photo:
        await bot.send_chat_action(message.chat.id, "typing")
        await asyncio.sleep(random.uniform(0.5, 2))
        reply = await ask_ari_with_context(user_id, message.text)
        user_context[user_id].append({"role": "assistant", "text": reply})
        name = mem.get("name")
        if name:
            reply = reply.replace("🦊", f"🦊 {name},")
        if mood == "positive":
            prefix = random.choice(loc["mood_positive"])
            reply = prefix + " " + reply
        elif mood == "negative":
            prefix = random.choice(loc["mood_negative"])
            reply = prefix + "\n" + reply
        elif mood == "neutral" and random.random() < 0.3:
            prefix = random.choice(loc["mood_neutral"])
            reply = prefix + " " + reply
        if random.random() < 0.2:
            reply = random.choice(loc["compliments"]) + "\n" + reply
        await message.answer(reply)
        return

    if cur is not None:
        return

    if any(p in message.text.lower() for p in ["что ты умеешь", "что умеешь"]):
        prompt = loc["what_prompt"]
        reply = await ask_ari(user_id, prompt)
        await message.answer(reply)
        return

    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(random.uniform(0.5, 2))
    reply = await ask_ari_with_context(user_id, message.text)
    user_context[user_id].append({"role": "assistant", "text": reply})
    name = mem.get("name")
    if name:
        reply = reply.replace("🦊", f"🦊 {name},")
    if mood == "positive":
        prefix = random.choice(loc["mood_positive"])
        reply = prefix + " " + reply
    elif mood == "negative":
        prefix = random.choice(loc["mood_negative"])
        reply = prefix + "\n" + reply
    elif mood == "neutral" and random.random() < 0.3:
        prefix = random.choice(loc["mood_neutral"])
        reply = prefix + " " + reply
    if random.random() < 0.2:
        reply = random.choice(loc["compliments"]) + "\n" + reply
    await message.answer(reply)

# ---------- Сохранение последнего фото ----------
@dp.message(F.photo)
async def save_last_photo(message: Message):
    user_last_photo[str(message.from_user.id)] = message.photo[-1].file_id

# ---------- Документы ----------
@dp.message(F.document)
async def handle_document(message: Message, state: FSMContext):
    await message.answer(LOCALE["ru"]["document_error"])

# ---------- Inline-режим 2.0 ----------
@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    query = inline_query.query.strip().lower()
    if query.startswith("style "):
        style_id = "style_" + query[6:].strip()
        if style_id in FILM_PROMPTS:
            result = InlineQueryResultArticle(
                id="1",
                title=f"Применить стиль {FILM_PROMPTS[style_id]}",
                description="Нажми, чтобы открыть бота и выбрать стиль",
                input_message_content=InputTextMessageContent(message_text=f"/start {style_id}"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Применить стиль", url=f"https://t.me/{(await bot.me()).username}?start={style_id}")]
                ])
            )
            await inline_query.answer([result], cache_time=1)
            return
    result = InlineQueryResultArticle(
        id="1", title="Открыть Ари", description="Начать диалог с кибер-лисичкой",
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
    save_memory()

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
