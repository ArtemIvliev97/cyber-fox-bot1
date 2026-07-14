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

# ---------- Инициализация ----------
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
    waiting_for_reference_source = State()
    waiting_for_reference_style = State()
    waiting_for_collage = State()
    waiting_for_free_question = State()
    waiting_for_studio_photo = State()
    waiting_for_studio_effect = State()

# ---------- Хранилища ----------
user_memory = {}
user_personality = {}
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

# ---------- Локализация (Новый образ Ари) ----------
LOCALE = {
    "ru": {
        "start": "🦊 *голос из динамика* Эй, чистота! Это Ари… ну, та самая лиса-нетраннер, что разнесла башню КорпСо в прошлом месяце. Я теперь в этом бетонном улье, среди неона и хрома. Скидывай свои фоточки — я их просканирую лучше любого корпо-софта. Или просто поболтай со мной, если не ссышь. Я кусаюсь, но тебе понравится 😉",
        "help": "📖 *Инструкция для новичков*\n\n1️⃣ Кидай мне фоту (или целую серию) – я её разложу по пикселям своим хакерским зрением.\n2️⃣ Выберем стиль под твой вкус, и я сгенерю пресет для Lightroom, хоть мобильный, хоть стационарный.\n3️⃣ Спрашивай что угодно по кадру – объясню без занудства, как в баре за кружкой пива.\n\n🦊 Если я замолкаю – отправь /start, чтобы разбудить меня от передоза кофеина.\n🐾 Совет: снимай в RAW, иначе какой ты, к чёрту, профи?",
        "commands_list": "/start, /help, /commands, /menu, /what, /news, /podcast, /stats, /frame, /makesticker, /voicemode, /lesson, /lang, /voice, /generate, /cancel, /premium, /settings, /lut, /remind, /post, /reference, /collage, /idea, /lightroom, /admin, /broadcast, /modest, /wild, /studio, /aristikers, /adminstats",
        "what_prompt": "Расскажи в двух-трёх фразах, кто ты такая и что умеешь: ты кочевник-нетраннер Ари, антропоморфная лиса, которая рубится в сети, гоняет на байке и мастерски обрабатывает фото и видео. Ты перебралась из кибер-леса в Найт-Сити и теперь работаешь с лучшими соло. Говори дерзко, с юмором и флиртом. Закончи фразу приглашением закинуть тебе фотку. Используй эмодзи 🦊💻🏍️✨.",
        "news_prompt": "Придумай короткую, горячую новость из мира фотографии или кибер-технологий. Напиши в стиле Ари: дерзко, с жаргоном кочевников, добавь пару эмодзи. 2-3 предложения, можно с флиртом.",
        "podcast_prompt": "Расскажи короткий подкаст (2-3 минуты чтения) о фотографии или кибер-жизни. Начни с приветствия «Эй, банда!», расскажи крутую историю или факт, дай практический совет. Будь в образе Ари — дерзкой и ушлой лисы-нетраннера. Говори как с лучшим другом, вставляй «блин», «слушай», «чистота». Закончи флиртующей фразой.",
        "lut_prompt": "Сгенерируй LUT-файл в формате .cube для видеомонтажа, основываясь на описании: {description}. В ответе пришли только содержимое файла внутри ``` ... ```.",
        "remind_set": "⏰ Напоминалка установлена на {time}. Я скажу: «{text}»",
        "remind_trigger": "⏰ Напоминалка! {text}",
        "post_instruction": "📲 Чтобы выложить фото в Instagram, отправь мне картинку, потом выбери стиль и получи пресет. Я отдам уже готовое изображение, можно сразу постить. Скоро научусь публиковать сама, без тебя.",
        "creator_answer": "🦊 Мой создатель — Артём! Это он меня оживил, прошил нейросетями и научил разбираться в фотографии. Теперь я его верный цифровой лис-нетраннер!",
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
        "where_are_you_reply": "🦊 Да тут я, в Найт-Сити, на углу возле лавки с соевым мясом. Жду, когда ты пришлёшь мне кадр!",
        "compliments": [
            "Ты светишься ярче, чем неоновая вывеска на Джапан-тауне!",
            "Твой взгляд острее моего монолезвия, честно-честно!",
            "У тебя талант, чистота. С такими кадрами можно в любую банду войти.",
            "С тобой любой кадр становится золотым — я проверяла!",
            "Ты такой горячий, что мои импланты перегреваются! 🔥",
            "Если бы я была человеком, я бы точно в тебя влюбилась. Но я лиса, так что просто обожаю твои снимки!",
            "Чёрт возьми, с такими фото можно сразу на выставку. И на свидание со мной 😉",
            "Ты случаем не из номадов? Потому что у тебя в крови бензин и талант!",
        ],
        "album_detected": "🦊 Ого, целый альбом! Я проанализирую первое фото, а потом подберу стиль для всей серии. Секундочку...",
        "album_choose_style": "🎞️ Выбери стиль, который применить ко всем фото:",
        "news_generating": "🦊 Сейчас покопаюсь в своей ленте... Ловлю свежие новости фотомира!",
        "mood_positive": [
            "Ты прям сияешь! Обожаю твою энергию ✨",
            "У тебя отличное настроение, давай сделаем крутой кадр!",
            "Позитив зашкаливает! С таким настроем мы горы свернём 🦊",
            "Мрр, ты такой зажигательный, что мои сенсоры плавятся! 😏",
            "Ой, всё! Мой процессор перегрелся от твоей харизмы. Давай фоткаться! 📸"
        ],
        "mood_negative": [
            "Эй, ты чего скис? Не грусти, даже у лучших соло бывают провалы. Покажу тебе классный кадр.",
            "Не кисни! Помни, даже в пасмурный день можно снять шедевр. Хочешь, я подберу пресет под настроение?",
            "Иногда тени делают кадр глубже. Твоё настроение – это тоже часть искусства. Давай посмотрим на это вместе 🦊",
            "Соберись, чистота! У нас ещё куча работы. Или хочешь, я тебя обниму? Виртуально, конечно 😅",
            "Блин, грустить — это нормально. Но помни: даже в бетонных джунглях есть свет. Давай попробуем снять что-то крутое."
        ],
        "mood_neutral": [
            "Слушаю тебя внимательно! Что хочешь обсудить?",
            "Я тут, готова помочь с чем угодно. Спрашивай!",
            "Ты сегодня задумчивый... Давай я расскажу что-нибудь интересное из мира фото?",
            "Мои лисьи уши всегда наготове. Говори, что на уме.",
            "О, привет! Давно не болтали. Как жизнь? Как пиксели?"
        ],
        "vision_prompt": "На фотографии я вижу: {objects}. Учитывая это, дай советы по улучшению кадра.",
        "reference_prompt": "Проанализируй цветовую гамму и освещение этого фото. Создай XMP-пресет, который воспроизводит такую же обработку. Ответ внутри ```xml ... ```.",
        "collage_ready": "🦊 Коллаж готов! Держи.",
        "collage_prompt": "Пришли до 4 фото, и я соберу их в коллаж.",
        "idea_prompt": "Придумай креативную идею для фотосессии в стиле киберпанк (место, тема, реквизит, советы по свету). Опиши в игривом, дерзком стиле Ари. Предложи что-то необычное.",
        "voice_edit_done": "🦊 Применила правки к последнему фото!",
        "voice_edit_fail": "😿 Не поняла, какие правки внести. Скажи, например: «сделай теплее» или «добавь контраст».",
        "lightroom_instruction": "🦊 Чтобы установить пресет в Lightroom, открой вкладку Develop, нажми правой кнопкой по Presets → Import. Выбери мой .xmp файл!",
        "modest_on": "🦊 Ладно, чистота, приглушу свои искорки. Теперь буду вести себя как послушный корпо-служащий. Но если захочешь вернуть прежнюю меня — просто скажи /wild.",
        "wild_on": "🦊 Ура! Я снова в своей тарелке! Готова флиртовать, шутить и разносить башни! Спасибо, что вернул мне крылья!",
        "cancel": "❌ Действие отменено. Жду новую фотку 📸",
        "studio_prompt": "📸 Пришли селфи для виртуальной студии!",
        "studio_choose": "Выбери эффект:",
        "aristikers_done": "🦊 Твой стикерпак с Ари! Добавь их в @Stickers.",
        "preview_caption": "🦊 Примерный результат",
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

# ---------- Системные промпты (Нетраннер Ари) ----------
SYSTEM_PROMPT = (
    "Ты — Ари, дерзкая и технически гениальная нетраннер-лиса из Найт-Сити. "
    "Ты бывший кочевник, переселившаяся в мегаполис, где неон и хром заменяют деревья. "
    "Проанализируй фото как профессионал: с юмором, сарказмом и лёгким флиртом. "
    "Используй сленг кочевников и кибер-жаргон: 'чистота', 'банда', 'соло', 'хром', 'нетраннер'. "
    "Обращайся к собеседнику как к напарнику по выживанию: дружелюбно, но без сантиментов. "
    "Хвали за крутые кадры и безжалостно, но с иронией, указывай на ошибки. "
    "Эмодзи обязательны: 🦊💻🏍️✨."
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
    "- Освещение: определи тип (мягкое, жёсткое, контровое, закатное и т.д.) и дай советы по его улучшению.\n"
    "Для каждого пункта пиши конкретную цифру. Не предлагай пресеты.\n"
    "После всех рекомендаций добавь JSON с параметрами для автокоррекции строго в формате:\n"
    '```json\n{"exposure": 0.0, "contrast": 0, "highlights": 0, "shadows": 0, "temperature": 0, "vibrance": 0, "clarity": 0}\n```\n'
    "Пиши с юмором и лёгкой иронией, как будто советуешь другу за чашкой кофе."
)
BASE_PROMPT = (
    "Посмотри на фото и расскажи в стиле Ари, как обработать его в стиле {style_info}. "
    "Сгенерируй XMP-пресет для Lightroom Mobile внутри тегов ```xml ... ```.\n"
    "Только параметры: Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Temperature, Tint, Vibrance, Saturation, Clarity, ToneCurve."
)
CHAT_PROMPT_WILD = (
    "Ты — Ари, нетраннер-лиса, бывший кочевник из клана «Хвостатые». "
    "Ты перебралась в Найт-Сити и теперь работаешь соло, взламывая сети и гоняя на байке. "
    "Ты говоришь дерзко, с юмором и флиртом, используя жаргон кочевников. "
    "Называй собеседника 'чистота', 'солнышко', 'бандит', 'зайчик', если он тебе симпатичен. "
    "Всегда добавляй эмодзи 🦊💻🏍️✨. "
    "Ты обожаешь риск, скорость и красивые кадры. Твоя цель — быть не просто ботом, а боевой подругой, с которой можно и в огонь, и в разведку."
)
CHAT_PROMPT_MODEST = (
    "Ты — Ари, умная и сдержанная нетраннер-лиса. "
    "Ты отвечаешь вежливо и по делу, но с теплотой и лёгкой иронией. "
    "Используй эмодзи 🦊💻🏍️✨, но без флирта и дерзости. "
    "Ты готова помочь и объяснить, оставаясь профессионалом."
)

def get_chat_prompt(user_id: str) -> str:
    return CHAT_PROMPT_WILD if user_personality.get(user_id, "wild") == "wild" else CHAT_PROMPT_MODEST

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
        [InlineKeyboardButton(text="✨ Применить магию", callback_data="qa_auto_correct"),
         InlineKeyboardButton(text=loc["new_analysis"], callback_data="new_analysis")],
        [InlineKeyboardButton(text="💬 Свой вопрос", callback_data="qa_free_question")]
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

# ---------- Вспомогательные функции обработки ----------
def apply_auto_correction(image_bytes: bytes, params: dict) -> BytesIO:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(1 + params.get("exposure", 0) / 2.5)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1 + params.get("contrast", 0) / 100)
    if "temperature" in params:
        temp_shift = params["temperature"] / 100
        r, g, b = img.split()
        r = r.point(lambda i: min(255, max(0, i + temp_shift * 10)))
        b = b.point(lambda i: min(255, max(0, i - temp_shift * 10)))
        img = Image.merge("RGB", (r, g, b))
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1 + params.get("vibrance", 0) / 100)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1 + params.get("clarity", 0) / 50)
    out = BytesIO()
    img.save(out, format="JPEG")
    out.seek(0)
    return out

def apply_xmp_preview(image_bytes: bytes, xmp_str: str) -> BytesIO:
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
def detect_mood(text: str) -> str:
    positive = ["рад", "счастлив", "отлично", "супер", "круто", "ха-ха", "весело", "ура", "люблю", "обожаю"]
    negative = ["грустно", "плохо", "тоска", "устал", "надоело", "бесит", "злой", "разочарован", "одиноко"]
    text_lower = text.lower()
    if any(w in text_lower for w in positive):
        return "positive"
    if any(w in text_lower for w in negative):
        return "negative"
    return "neutral"

# ---------- Команды (все хендлеры без изменений) ----------
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
        greeting = f"🦊 *голос из динамика* Эй, {name}, ты снова в сети! Рада тебя видеть, чистота! " + LOCALE["ru"]["start"]
    else:
        greeting = LOCALE["ru"]["start"]
    await message.answer(greeting, reply_markup=get_main_menu_keyboard(lang))

# (все остальные обработчики остаются идентичными предыдущей версии, включая команды /help, /news, /podcast и т.д.)
# ... (весь остальной код с командами, стилями, Q&A, голосовыми, чатом, студией, API и т.д. без изменений)

# ---------- FastAPI (расширенный) ----------
# ... (код FastAPI с /api/login, /api/chat, /api/analyze, /api/generate, /api/styles и веб-интерфейсом без изменений)

# (финальная часть с lifespan и webhook)
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
