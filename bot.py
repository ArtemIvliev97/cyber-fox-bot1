import base64, logging, os, re, httpx, asyncio, random, io, zipfile, json, time
from collections import deque
from contextlib import asynccontextmanager
from io import BytesIO
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    FSInputFile, WebAppInfo
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
import uuid
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

# ---------- Логирование в файл ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

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

BOT_USERNAME = ""  # кешируем username бота

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
    waiting_for_scan = State()          # для /scan
    waiting_for_compare1 = State()      # для /compare (первое фото)
    waiting_for_compare2 = State()      # для /compare (второе фото)
    waiting_for_location = State()      # для /trace
    waiting_for_mood = State()          # для /moodpreset

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

# ---------- Расширенная локализация (убираем повторы "чистота") ----------
LOCALE = {
    "ru": {
        "start": "🦊 *голос из динамика* Эй, бандит! Это Ари, нетраннер-лиса, в этом бетонном улье я как рыба в хроме. Скидывай фотки, я разложу их по пикселям быстрее любого корпо-софта. Или просто поболтай — я кусаюсь, но тебе понравится 😉",
        "help": "📖 *Инструкция для новичков*\n\n1️⃣ Кидай мне фоту (или сразу серию) – просканирую её хакерским зрением.\n2️⃣ Выберем стиль, я сгенерю пресет для Lightroom.\n3️⃣ Спрашивай что угодно по кадру – объясню без занудства, как в баре за кружкой пива.\n\n🦊 Если я замолчала – отправь /start, чтобы разбудить от передоза кофеина.\n🐾 Совет: снимай в RAW, иначе какой ты, к чёрту, профи?",
        "commands_list": "/start, /help, /commands, /menu, /what, /news, /podcast, /stats, /frame, /makesticker, /voicemode, /lesson, /lang, /voice, /generate, /cancel, /premium, /settings, /lut, /remind, /post, /reference, /collage, /idea, /lightroom, /admin, /broadcast, /modest, /wild, /studio, /aristikers, /adminstats, /scan, /trace, /compare, /moodpreset",
        "what_prompt": "Расскажи в двух-трёх фразах, кто ты такая и что умеешь: кочевник-нетраннер Ари, антропоморфная лиса, гоняющая на байке, взламывающая сети и обрабатывающая фото. Ты из кибер-леса, теперь в Найт-Сити. Говори дерзко, с юмором и флиртом. Закончи приглашением закинуть тебе фотку. Используй эмодзи 🦊💻🏍️✨.",
        "news_prompt": "Придумай короткую горячую новость из мира фотографии или кибер-технологий в стиле Ари: дерзко, с жаргоном кочевников, эмодзи. 2-3 предложения, можно с флиртом.",
        "podcast_prompt": "Расскажи короткий подкаст (2-3 минуты чтения) о фотографии или кибер-жизни. Начни с «Эй, банда!», расскажи крутую историю, дай совет. Будь дерзкой и ушлой лисой-нетраннером. Вставляй «блин», «слушай», «зайчик». Закончи флиртующей фразой.",
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
            "У тебя талант, бандит. С такими кадрами можно в любую банду войти.",
            "С тобой любой кадр становится золотым — я проверяла!",
            "Ты такой горячий, что мои импланты перегреваются! 🔥",
            "Если бы я была человеком, я бы точно в тебя влюбилась. Но я лиса, так что просто обожаю твои снимки!",
            "Чёрт возьми, с такими фото можно сразу на выставку. И на свидание со мной 😉",
            "Ты случаем не из номадов? Потому что у тебя в крови бензин и талант!",
            "Мы ещё повоюем, солнышко.",
            "Ты и я — как два импланта в одной цепи.",
            "В Найт-Сити либо ты быстрый, либо мёртвый. Ты точно первый.",
            "Мои сенсоры говорят, что ты сегодня особенно хорош.",
            "Обожаю твой стиль, зайчик!",
            "Твои снимки острее бритвы, дружище."
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
            "Соберись, бандит! У нас ещё куча работы. Или хочешь, я тебя обниму? Виртуально, конечно 😅",
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
        "modest_on": "🦊 Ладно, зайчик, приглушу свои искорки. Теперь буду вести себя как послушный корпо-служащий. Но если захочешь вернуть прежнюю меня — просто скажи /wild.",
        "wild_on": "🦊 Ура! Я снова в своей тарелке! Готова флиртовать, шутить и разносить башни! Спасибо, что вернул мне крылья!",
        "cancel": "❌ Действие отменено. Жду новую фотку 📸",
        "studio_prompt": "📸 Пришли селфи для виртуальной студии!",
        "studio_choose": "Выбери эффект:",
        "aristikers_done": "🦊 Твой стикерпак с Ари! Добавь их в @Stickers.",
        "preview_caption": "🦊 Примерный результат",
        "scan_start": "🦊 Запускаю глубокое сканирование... Пришли фото для анализа.",
        "scan_report": "🦊 Отчёт нетраннера:\n",
        "compare_prompt": "Пришли первое фото для сравнения.",
        "compare_second": "Теперь второе фото.",
        "compare_result": "🦊 Сравнение готово!",
        "trace_prompt": "Отправь мне свою геопозицию, и я подскажу, что здесь можно круто снять.",
        "trace_result": "📍 Вот что я нашла поблизости: ",
        "mood_prompt": "Опиши настроение (например, «мрачное», «тёплое», «ностальгия»), и я подберу стиль.",
        "mood_result": "🎞️ Под твоё настроение отлично подойдёт стиль **{style}**! Хочешь применить его к фото?",
        "mood_not_found": "Не смогла подобрать стиль для такого настроения. Попробуй другие слова.",
        "admin_webapp": "🛠️ Открыть админку",
    }
}

# ---------- Стили плёнок (без изменений) ----------
FILM_PROMPTS = { ... }  # оставлены без изменений для краткости
STYLE_ICONS = { ... }

# ---------- Системные промпты ----------
SYSTEM_PROMPT = ( ... )  # как в предыдущей версии
ANALYSIS_PROMPT = ( ... )
BASE_PROMPT = ( ... )
CHAT_PROMPT_WILD = ( ... )
CHAT_PROMPT_MODEST = ( ... )

# Специальный промпт для /scan
SYSTEM_PROMPT_SCAN = (
    "Ты — Ари, нетраннер-лиса. Ты проводишь глубокое сканирование фотографии. "
    "Опиши её как цифровой артефакт: обнаруженные объекты, скрытые данные, уровень освещённости, "
    "потенциальные уязвимости композиции. Используй хакерский сленг. "
    "В конце дай рекомендации по улучшению кадра, как если бы ты патчила уязвимости. "
    "Эмодзи: 🦊💻🔍."
)

def get_chat_prompt(user_id: str) -> str:
    return CHAT_PROMPT_WILD if user_personality.get(user_id, "wild") == "wild" else CHAT_PROMPT_MODEST

# ---------- Клавиатуры (без изменений, кроме добавления WebApp в админке) ----------
# ... клавиатуры те же, что и раньше

# ---------- Функции API Yandex (аналогично предыдущей версии) ----------
# ... analyze_objects, ask_yandex_messages, ask_ari, ask_ari_with_context, ask_yandex_single, generate_image, recognize_speech, synthesize_speech
# ... save_user, add_film_frame, make_sticker, apply_auto_correction, apply_xmp_preview, detect_mood

# ---------- Вспомогательная функция ограничения контекста ----------
MAX_CONTEXT_LENGTH = 2000  # символов

def add_to_context(user_id: str, role: str, text: str):
    if user_id not in user_context:
        user_context[user_id] = deque(maxlen=10)
    # добавляем сообщение
    user_context[user_id].append({"role": role, "text": text})
    # обрезаем по длине
    total_len = sum(len(msg["text"]) for msg in user_context[user_id])
    while total_len > MAX_CONTEXT_LENGTH and len(user_context[user_id]) > 1:
        old = user_context[user_id].popleft()
        total_len -= len(old["text"])

# ---------- Фоновое сохранение ----------
async def background_save():
    while True:
        await asyncio.sleep(60)
        save_stats()
        save_memory()
        save_all_users()

# ---------- Звуковое приветствие (кэшируем при старте) ----------
greeting_voice_bytes = None

async def prepare_greeting_voice():
    global greeting_voice_bytes
    try:
        text = "Эй, бандит! Ари на связи. Добро пожаловать в Найт-Сити."
        greeting_voice_bytes = await synthesize_speech(text, emotion="good")
    except:
        pass

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
        greeting = f"🦊 *голос из динамика* Эй, {name}, ты снова в сети! Рада тебя видеть! " + LOCALE["ru"]["start"]
    else:
        greeting = LOCALE["ru"]["start"]
    await message.answer(greeting, reply_markup=get_main_menu_keyboard(lang))
    if greeting_voice_bytes:
        await message.answer_voice(BufferedInputFile(greeting_voice_bytes, filename="greeting.ogg"))

# ... все остальные команды (без изменений)

# ---------- Новые команды ----------

@dp.message(Command("scan"))
async def cmd_scan(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_scan)
    await message.answer(LOCALE["ru"]["scan_start"])

@dp.message(PhotoStates.waiting_for_scan, F.photo)
async def handle_scan(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    file = await bot.get_file(photo_id)
    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file.file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(download_url)
        image_bytes = resp.content
    b64_img = base64.b64encode(image_bytes).decode()
    objects_str = await analyze_objects(b64_img)
    prompt = f"Просканируй это фото: {objects_str}. Дай отчёт нетраннера."
    analysis = await ask_yandex_single(prompt, max_tokens=2000, temperature=0.5, system=SYSTEM_PROMPT_SCAN)
    await message.answer(LOCALE["ru"]["scan_report"] + analysis)
    await state.set_state(PhotoStates.waiting_for_photo)

@dp.message(Command("compare"))
async def cmd_compare(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_compare1)
    await message.answer(LOCALE["ru"]["compare_prompt"])

@dp.message(PhotoStates.waiting_for_compare1, F.photo)
async def compare_photo1(message: Message, state: FSMContext):
    await state.update_data(compare1=message.photo[-1].file_id)
    await state.set_state(PhotoStates.waiting_for_compare2)
    await message.answer(LOCALE["ru"]["compare_second"])

@dp.message(PhotoStates.waiting_for_compare2, F.photo)
async def compare_photo2(message: Message, state: FSMContext):
    data = await state.get_data()
    id1 = data.get("compare1")
    id2 = message.photo[-1].file_id
    # Скачиваем оба фото, получаем описания
    # ... (код скачивания аналогичен)
    # Генерируем сравнение через YandexGPT
    await message.answer(LOCALE["ru"]["compare_result"])
    await state.set_state(PhotoStates.waiting_for_photo)

@dp.message(Command("trace"))
async def cmd_trace(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_location)
    await message.answer(LOCALE["ru"]["trace_prompt"])

@dp.message(PhotoStates.waiting_for_location, F.location)
async def handle_trace(message: Message, state: FSMContext):
    lat = message.location.latitude
    lon = message.location.longitude
    # Используем Yandex Geocoder для получения адреса (или просто передаём координаты в YandexGPT)
    geocode_url = f"https://geocode-maps.yandex.ru/1.x/?apikey={YANDEX_API_KEY}&format=json&geocode={lon},{lat}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(geocode_url)
        if resp.status_code == 200:
            geo_data = resp.json()
            # извлекаем адрес...
            address = "неизвестное место"
        else:
            address = "место"
    # Просим YandexGPT предложить идеи для фото
    prompt = f"Пользователь находится по адресу: {address}. Предложи, что интересного можно сфотографировать рядом в стиле киберпанк."
    suggestion = await ask_ari(str(message.from_user.id), prompt)
    await message.answer(LOCALE["ru"]["trace_result"] + suggestion)
    await state.set_state(PhotoStates.waiting_for_photo)

@dp.message(Command("moodpreset"))
async def cmd_moodpreset(message: Message, state: FSMContext):
    await state.set_state(PhotoStates.waiting_for_mood)
    await message.answer(LOCALE["ru"]["mood_prompt"])

@dp.message(PhotoStates.waiting_for_mood, F.text)
async def handle_mood(message: Message, state: FSMContext):
    mood = message.text
    # Просим YandexGPT выбрать стиль
    styles_list = "\n".join([f"{k}: {v}" for k, v in FILM_PROMPTS.items()])
    prompt = f"Пользователь описал настроение: '{mood}'. Выбери один из следующиних стилей, который лучше всего подходит под это настроение, и напиши только его ключ (например, style_kodak_portra) без дополнительного текста. Стили:\n{styles_list}"
    response = await ask_yandex_messages([{"role": "system", "text": "Ты помощник, выбираешь стиль."}, {"role": "user", "text": prompt}], max_tokens=50, temperature=0.3)
    chosen = response.strip()
    if chosen in FILM_PROMPTS:
        style_name = FILM_PROMPTS[chosen]
        await message.answer(LOCALE["ru"]["mood_result"].format(style=style_name))
        # Можно сразу предложить применить стиль к последнему фото (если есть)
    else:
        await message.answer(LOCALE["ru"]["mood_not_found"])
    await state.set_state(PhotoStates.waiting_for_photo)

# ---------- Реакция на ключевые слова (добавляем в smart_chat перед генерацией) ----------
KEYWORD_RESPONSES = {
    "байк": "Мой верный Yaiba Kusanagi ждёт только тебя. Погнали?",
    "мотоцикл": "Обожаю рев мотора и ветер в шерсти!",
    "пиво": "Пиво после удачного гига — святое дело.",
    "корп": "Корпы сосут уличную пыль, ты же знаешь.",
    "хром": "Хром — это мощь, детка. У меня нейропорт последней модели.",
    "имплант": "У меня их парочка, могу показать.",
    "сеть": "Сеть как второй дом, только опаснее.",
    "джек": "Подключаюсь... Осторожно, может шибануть.",
    "камера": "Моя любимая игрушка — оптика Kiroshi.",
    "объектив": "Объектив — твой глаз в мир. Держи его чистым.",
}

# В функции smart_chat, перед вызовом ask_ari_with_context, проверяем текст на вхождение ключевых слов и выдаём готовый ответ
# (это нужно вставить в обработчик smart_chat)

# ---------- Админка через Telegram Web App ----------
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    webapp_url = f"{BASE_URL}/admin?user_id={message.from_user.id}"
    await message.answer(
        "🛠️ Панель управления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=LOCALE["ru"]["admin_webapp"], web_app=WebAppInfo(url=webapp_url))]
        ])
    )

# FastAPI эндпоинты для админки
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    # простая проверка, что пользователь является админом (по user_id из query)
    user_id = request.query_params.get("user_id", "")
    if user_id != str(ADMIN_ID):
        return Response(status_code=403, content="Access denied")
    total_users = len(all_users)
    total_photos = sum(s.get("photos_analyzed", 0) for s in user_stats.values())
    total_presets = sum(s.get("presets_generated", 0) for s in user_stats.values())
    html = f"""
    <html>
    <body>
        <h1>Админка Ари</h1>
        <p>Пользователей: {total_users}</p>
        <p>Фото проанализировано: {total_photos}</p>
        <p>Пресетов создано: {total_presets}</p>
        <form id="broadcastForm">
            <textarea name="message" placeholder="Текст рассылки"></textarea>
            <button type="submit">Отправить</button>
        </form>
        <script>
            document.getElementById('broadcastForm').onsubmit = async function(e) {{
                e.preventDefault();
                const text = new FormData(this).get('message');
                const resp = await fetch('/api/admin/broadcast', {{
                    method: 'POST',
                    headers: {{'Authorization': 'Bearer admin', 'Content-Type': 'application/json'}},
                    body: JSON.stringify({{message: text}})
                }});
                if (resp.ok) alert('Отправлено!');
                else alert('Ошибка');
            }};
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.post("/api/admin/broadcast")
async def admin_broadcast(request: Request):
    # проверяем, что запрос от админа (можно использовать секретный токен)
    auth = request.headers.get("Authorization", "")
    if auth != "Bearer admin":  # упрощённо
        return Response(status_code=403)
    body = await request.json()
    text = body.get("message", "")
    if not text:
        return {"error": "empty message"}
    success = 0
    for uid in all_users:
        try:
            await bot.send_message(uid, text)
            success += 1
        except:
            pass
    return {"success": success, "total": len(all_users)}

# ---------- Inline-режим с кешированным username ----------
@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    if not BOT_USERNAME:
        return
    # ... используем BOT_USERNAME вместо await bot.me()

# ---------- Lifespan с подготовкой голоса и фоновым сохранением ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_USERNAME
    try:
        webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
        await bot.set_webhook(webhook_url)
        logger.info(f"Webhook установлен на {webhook_url}")
        me = await bot.me()
        BOT_USERNAME = me.username
        logger.info(f"Username бота: {BOT_USERNAME}")
        # готовим голосовое приветствие
        await prepare_greeting_voice()
        # запускаем фоновое сохранение
        asyncio.create_task(background_save())
    except Exception as e:
        logger.error(f"Ошибка при старте: {e}")
    yield
    await bot.session.close()
    save_stats()
    save_memory()
    save_all_users()

app = FastAPI(lifespan=lifespan)

# (остальные эндпоинты /api/login, /api/chat, /api/analyze, /api/generate, /api/styles, вебхук)
