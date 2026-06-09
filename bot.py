import base64
import logging
import os
import re
import httpx
import asyncio
import random
import io

from contextlib import asynccontextmanager
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

# ---------- Локализация ----------
LOCALE = {
    "ru": {
        "start": "🦊 Привет! На связи Ари — твой личный объектив в мире классного контента! 📸✨ Я вижу этот мир чертовски красивым и помогу тебе сделать так, чтобы все вокруг тоже это заметили. Можешь сразу прислать фото, и я проанализирую его, или поболтаем — как хочешь! 😉",
        "help": "📖 <b>Инструкция по фокусу</b>\n\n1️⃣ Отправь мне фотографию — я сразу проанализирую ошибки и дам советы.\n2️⃣ Потом сможешь выбрать плёночный стиль, и я сгенерирую пресет для Lightroom.\n3️⃣ После анализа можешь задать вопросы по кадру.\n\n🦊 Если я не отвечаю — отправь /start, чтобы разбудить меня снова.\n🐾 Совет: снимай в RAW для максимального качества!",
        "commands_list": (
            "📋 <b>Доступные команды</b>\n\n"
            "/start — Пробудить Ари и начать диалог\n"
            "/help — Инструкция по использованию\n"
            "/commands — Этот список команд\n"
            "/menu — Главное меню с кнопками\n"
            "/what — Что умеет Ари\n"
            "/lang — Сменить язык (русский/English)\n"
            "/voice — Проверить голос Ари (если включён)\n"
            "/generate — Сгенерировать изображение по описанию\n"
            "/cancel — Отменить текущее действие\n"
            "/premium — Информация о премиум-возможностях\n"
            "/settings — Настройки (скоро)\n"
            "/broadcast — Рассылка (только для админа)"
        ),
        "settings": "🛠 Тюнинг объектива\n\nЗдесь скоро появится настройка качества обработки, выбор формата пресетов и фильтры.\nПока я использую стандартный профиль: мягкий контраст, точные цвета и максимум деталей.\n\n⚙️ Ожидай обновлений — я стану ещё гибче!",
        "premium": "⚡️ Кибер-прокачка\n\nС режимом PREMIUM я смогу:\n• Обрабатывать серии фото за раз\n• Генерировать пресеты в .xmp и .dng\n• Давать расширенный анализ с гистограммой\n• Работать с видео-кадрами\n\nПока этот модуль в разработке, но ты уже пользуешься базовыми супер-силами бесплатно! 🦊",
        "cancel": "🦊 Предыдущее действие отменено. Жду новое фото!",
        "menu": "🦊 Главное меню Ари",
        "what": "🦊 О, я умею видеть то, что скрыто...",
        "choose_style": "🎞️ Выбери стиль",
        "skip_style": "✅ Разбор завершён! Жду новое фото 📸",
        "all_styles": "📋 Все стили",
        "analysis_start": "🦊 Хмм, сканирую взглядом... Дай мне пару сек, подкручу настройки магии! 👀",
        "small_photo": "Ой, какая крошечная пиксельная картинка!...",
        "document_error": "Упс! Похоже, ты прислал файл, а не фото. 📦",
        "style_processing": "Ловлю фокус... Навожу резкость... Хитрые алгоритмы уже шуршат! 🐾⚙️",
        "preset_caption": "🦊 Твой пресет для Lightroom (включая Mobile)!",
        "qa_choose": "Есть вопросы по кадру? Выбери тему:",
        "qa_done": "✅ Разбор завершён! Жду новое фото 📸",
        "main_focus": "📸 Фокус наведён! Присылай своё фото, и я сразу всё расскажу.",
        "main_magic": "✨ Магия ИИ-фильтров",
        "main_crop": "✂️ Функция «Обрезать лишнее» пока в разработке...",
        "main_gallery": "🦊 Твоя галерея пока пуста...",
        "main_energy": "💎 Энергия Ари: сейчас безлимитный доступ.",
        "main_generate": "🎨 Генератор изображений",
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
    },
    "en": {
        "start": "🦊 Hi! I'm Ari, your personal lens...",
        "help": "📖 <b>How to focus</b>\n\n1️⃣ Send me a photo — I'll analyze mistakes and give advice.\n2️⃣ Then choose a film style, and I'll generate a Lightroom preset.\n3️⃣ After analysis, you can ask questions about the shot.\n\n🦊 If I don't respond — send /start to wake me up.\n🐾 Tip: shoot in RAW for the best quality!",
        "commands_list": (
            "📋 <b>Available commands</b>\n\n"
            "/start — Wake up Ari and start chatting\n"
            "/help — How to use\n"
            "/commands — This list of commands\n"
            "/menu — Main menu with buttons\n"
            "/what — What Ari can do\n"
            "/lang — Switch language (Russian/English)\n"
            "/voice — Test Ari's voice (if enabled)\n"
            "/generate — Generate an image from a description\n"
            "/cancel — Cancel current action\n"
            "/premium — About premium features\n"
            "/settings — Settings (coming soon)\n"
            "/broadcast — Broadcast message (admin only)"
        ),
        "settings": "🛠 Lens tuning...",
        "premium": "⚡️ Cyber upgrade...",
        "cancel": "🦊 Action cancelled...",
        "menu": "🦊 Ari's main menu",
        "what": "🦊 Oh, I can see what's hidden!...",
        "choose_style": "🎞️ Choose a style",
        "skip_style": "✅ Analysis finished! Send another photo 📸",
        "all_styles": "📋 All styles",
        "analysis_start": "🦊 Hmm, scanning...",
        "small_photo": "Oh, such a tiny picture...",
        "document_error": "Oops! Looks like you sent a file...",
        "style_processing": "Catching focus... Algorithms are rustling!",
        "preset_caption": "🦊 Your Lightroom preset (including Mobile)!",
        "qa_choose": "Any questions about the shot? Choose a topic:",
        "qa_done": "✅ Analysis done! Send a new photo 📸",
        "main_focus": "📸 Focus on!",
        "main_magic": "✨ AI magic",
        "main_crop": "✂️ Feature in development...",
        "main_gallery": "🦊 Your gallery is empty...",
        "main_energy": "💎 Ari Energy: unlimited",
        "main_generate": "🎨 Image Generator",
        "main_commands": "📋 Commands",
        "lang_switched": "🦊 Language switched to English 🇬🇧",
        "generate_prompt": "🎨 Describe what you want to see...",
        "generating": "🦊 Drawing...",
        "generated": "✨ Here you go!",
        "generate_error": "😿 Couldn't generate the image...",
        "ask_for_photo": "🦊 To analyze a photo, please send it to me. I'll tell you what's wrong and how to fix it!",
        "busy_photo_override": "🦊 I see a new photo. I'll stop the previous process and start the analysis over.",
        "new_analysis": "🔄 New analysis",
        "voice_unrecognized": "🦊 Couldn't catch your voice. Maybe type it?",
        "voice_analysis_request": "🦊 To analyze a photo, just send me the picture, don't tell me about it 😉",
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

# ---------- Рекомендация стилей ----------
def suggest_styles(analysis_text: str):
    text = analysis_text.lower()
    scores = {}
    if any(w in text for w in ["тёпл", "тепл", "золот", "солнц"]):
        scores["style_kodak_portra"] = scores.get("style_kodak_portra", 0) + 1
        scores["style_kodak_gold"] = scores.get("style_kodak_gold", 0) + 1
    if any(w in text for w in ["холод", "синев", "неон"]):
        scores["style_cinestill"] = scores.get("style_cinestill", 0) + 2
        scores["style_fuji_superia"] = scores.get("style_fuji_superia", 0) + 1
    if any(w in text for w in ["контраст", "чёрн", "черн", "тен"]):
        scores["style_kodak_trix"] = scores.get("style_kodak_trix", 0) + 1
        scores["style_hdr"] = scores.get("style_hdr", 0) + 1
    if any(w in text for w in ["портрет", "лиц", "кож"]):
        scores["style_fuji_astia"] = scores.get("style_fuji_astia", 0) + 2
        scores["style_clean_portrait"] = scores.get("style_clean_portrait", 0) + 2
    if any(w in text for w in ["ярк", "насыщ", "сочн"]):
        scores["style_kodak_ektar"] = scores.get("style_kodak_ektar", 0) + 1
        scores["style_fuji_velvia"] = scores.get("style_fuji_velvia", 0) + 1
    if any(w in text for w in ["ноч", "город", "фонар"]):
        scores["style_night_city"] = scores.get("style_night_city", 0) + 2
    if not scores:
        scores["style_kodak_portra"] = 1
        scores["style_fuji_provia"] = 1
        scores["style_hasselblad"] = 1
    return sorted(scores, key=scores.get, reverse=True)[:3]

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
    "Ты — Ари, живая кибер-лисичка с характером. Будь эмоциональной, остроумной, используй эмодзи 🦊📸✨. Отвечай коротко, как друг."
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
        [InlineKeyboardButton(text="✂️ " + loc["main_crop"], callback_data="main_crop")],
        [InlineKeyboardButton(text="🦊 " + loc["main_gallery"], callback_data="main_gallery")],
        [InlineKeyboardButton(text="💎 " + loc["main_energy"], callback_data="main_energy")],
        [InlineKeyboardButton(text="🎨 " + loc["main_generate"], callback_data="main_generate")],
        [InlineKeyboardButton(text="📋 " + loc["main_commands"], callback_data="main_commands")],
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

async def generate_image(prompt: str) -> bytes | None:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandex-art/latest",
        "generationOptions": {"seed": random.randint(1, 1000000), "mimeType": "image/png", "temperature": 0.7},
        "messages": [{"text": prompt, "weight": 1}]
    }
    try:
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
    except Exception as e:
        logger.error(f"Image generation exception: {e}")
        return None

async def recognize_speech(audio_bytes: bytes, lang: str = "ru-RU") -> str:
    url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
    params = {"lang": lang, "format": "oggopus"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, params=params, content=audio_bytes, timeout=30.0)
        if resp.status_code == 200:
            return resp.json().get("result", "")
        else:
            logger.error(f"STT error: {resp.status_code} {resp.text}")
            return ""
    except Exception as e:
        logger.error(f"STT exception: {e}")
        return ""

def fix_ari_pronunciation(text: str) -> str:
    """Заменяет 'Ари' на 'А+ри', чтобы TTS ставила ударение на первый слог."""
    return re.sub(r'\bАри\b', 'А+ри', text)

async def synthesize_speech(text: str, lang: str = "ru-RU") -> bytes | None:
    """Синтезирует милую речь с случайным голосом, префиксом и мягкой скоростью."""
    cute_prefixes = [
        "Ой! ", "Хм-м... ", "Уи-и! ", "Слушай... ",
        "Ну что... ", "Эй! ", "", ""
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
        "emotion": "good",
        "speed": str(round(random.uniform(0.85, 0.95), 2)),
        "format": "oggopus",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, data=params, timeout=30.0)
        if resp.status_code == 200:
            return resp.content
        else:
            logger.error(f"TTS error with emotion: {resp.status_code} {resp.text}")
            if resp.status_code == 400:
                params.pop("emotion", None)
                params["speed"] = "1.0"
                async with httpx.AsyncClient() as client2:
                    resp2 = await client2.post(url, headers=headers, data=params, timeout=30.0)
                if resp2.status_code == 200:
                    return resp2.content
                else:
                    logger.error(f"TTS error without emotion: {resp2.status_code} {resp2.text}")
            return None
    except Exception as e:
        logger.error(f"TTS exception: {e}")
        return None

async def save_user(user_id: int):
    all_users.add(user_id)

# ---------- Обработчики команд ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    lang = "ru"
    await state.update_data(lang=lang)
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer(LOCALE[lang]["start"], reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await message.answer(LOCALE[lang]["menu"], reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("lang"))
async def cmd_lang(message: Message, state: FSMContext):
    data = await state.get_data()
    current_lang = data.get("lang", "ru")
    new_lang = "en" if current_lang == "ru" else "ru"
    await state.update_data(lang=new_lang)
    await message.answer(LOCALE[new_lang]["lang_switched"])

@dp.message(Command("voice"))
async def test_voice(message: Message):
    """Демонстрация милого голоса Ари с правильным ударением."""
    phrases = [
        "Привет! Я Ари, и мой голос стал ещё милее!",
        "Ой, кажется, у меня мурашки по лапкам от твоего внимания!",
        "Сегодня отличный день, чтобы сделать крутой кадр. Ты готов?"
    ]
    for phrase in phrases:
        corrected = fix_ari_pronunciation(phrase)
        voice_bytes = await synthesize_speech(corrected)
        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="ari_milaya.ogg")
            await message.answer_voice(voice_file)
        else:
            await message.answer("😿 Не получилось синтезировать голос.")

@dp.message(Command("what"))
async def cmd_what(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await message.answer(LOCALE[lang]["what"], reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    logger.info("Команда /help вызвана")
    data = await state.get_data()
    lang = data.get("lang", "ru")
    help_texts = {
        "ru": LOCALE["ru"]["help"],
        "en": LOCALE["en"]["help"]
    }
    await message.answer(help_texts.get(lang, help_texts["ru"]))

@dp.message(Command("commands"))
async def cmd_commands(message: Message, state: FSMContext):
    logger.info("Команда /commands вызвана")
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await message.answer(LOCALE[lang]["commands_list"])

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer(LOCALE[lang]["cancel"])

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Использование: /broadcast <текст>")
        return
    success = 0
    for user_id in all_users:
        try:
            await bot.send_message(user_id, text)
            success += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить пользователю {user_id}: {e}")
    await message.answer(f"Рассылка завершена. Отправлено {success}/{len(all_users)} пользователям.")

# ---------- Главное меню ----------
@dp.callback_query(F.data == "main_focus")
async def main_focus(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(LOCALE[lang]["main_focus"])
    await callback.answer()

@dp.callback_query(F.data == "main_magic")
async def main_magic(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    current_state = await state.get_state()
    if current_state == PhotoStates.waiting_for_style:
        await callback.message.edit_text(LOCALE[lang]["choose_style"], reply_markup=get_style_keyboard(lang))
    else:
        await state.clear()
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.message.edit_text("✨ " + LOCALE[lang]["main_magic"])
    await callback.answer()

@dp.callback_query(F.data == "main_crop")
async def main_crop(callback: CallbackQuery):
    await callback.message.edit_text("✂️ Функция «Обрезать лишнее» пока в разработке...")
    await callback.answer()

@dp.callback_query(F.data == "main_gallery")
async def main_gallery(callback: CallbackQuery):
    await callback.message.edit_text("🦊 Твоя галерея пока пуста...")
    await callback.answer()

@dp.callback_query(F.data == "main_energy")
async def main_energy(callback: CallbackQuery):
    await callback.message.edit_text("💎 Энергия Ари: сейчас ты пользуешься безлимитным доступом.")
    await callback.answer()

@dp.callback_query(F.data == "main_generate")
async def main_generate(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await state.set_state(PhotoStates.waiting_for_prompt)
    await callback.message.edit_text(LOCALE[lang]["generate_prompt"])
    await callback.answer()

@dp.callback_query(F.data == "main_commands")
async def main_commands(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(LOCALE[lang]["commands_list"])
    await callback.answer()

# ---------- Обработка фото ----------
@dp.message(F.photo)
async def handle_photo_any_state(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == PhotoStates.waiting_for_prompt:
        return

    await save_user(message.from_user.id)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]

    if current_state in [PhotoStates.waiting_for_style, PhotoStates.waiting_for_qa]:
        await message.answer(loc["busy_photo_override"])
    elif current_state != PhotoStates.waiting_for_photo:
        await message.answer("🦊 Новый кадр! Начинаю анализ.")

    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)

    try:
        file_info = await bot.get_file(photo_id)
        file_size_kb = file_info.file_size / 1024
        if file_size_kb < 5:
            await message.answer(loc["small_photo"])
            return
    except Exception:
        pass

    await message.answer(loc["analysis_start"])
    await bot.send_chat_action(message.chat.id, "typing")

    try:
        file_info = await bot.get_file(photo_id)
        file_path = file_info.file_path
        download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(download_url)
            image_bytes = resp.content
        b64_img = base64.b64encode(image_bytes).decode()

        exif_info = ""
        try:
            image_stream = io.BytesIO(image_bytes)
            tags = exifread.process_file(image_stream, details=False)
            if tags:
                exif_parts = []
                if 'EXIF ExposureTime' in tags:
                    exif_parts.append(f"Выдержка: {tags['EXIF ExposureTime']}")
                if 'EXIF FNumber' in tags:
                    exif_parts.append(f"Диафрагма: f/{tags['EXIF FNumber'].values[0]}")
                if 'EXIF ISOSpeedRatings' in tags:
                    exif_parts.append(f"ISO: {tags['EXIF ISOSpeedRatings']}")
                if 'EXIF FocalLength' in tags:
                    exif_parts.append(f"Фокусное: {tags['EXIF FocalLength']} мм")
                if exif_parts:
                    exif_info = "Реальные параметры съёмки: " + "; ".join(exif_parts) + "."
        except Exception as e:
            logger.warning(f"EXIF error: {e}")

        full_prompt = (exif_info + "\n" + ANALYSIS_PROMPT) if exif_info else ANALYSIS_PROMPT
        analysis_text = await ask_yandex(full_prompt, max_tokens="2000", temperature=0.4)
        await message.answer(analysis_text)

        recommended = suggest_styles(analysis_text)
        await state.update_data(b64_image=b64_img, lang=lang)
        await state.set_state(PhotoStates.waiting_for_style)
        await message.answer("Хочешь применить один из моих плёночных стилей? 🎞️", reply_markup=get_style_choice_keyboard(lang))
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await message.answer("😿 Что-то пошло не так во время анализа. Попробуй другое фото.")
        await state.set_state(PhotoStates.waiting_for_photo)

# ---------- Выбор стиля и генерация пресета ----------
@dp.callback_query(PhotoStates.waiting_for_style, F.data == "choose_style")
async def choose_style(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(LOCALE[lang]["choose_style"], reply_markup=get_style_keyboard(lang))
    await callback.answer()

@dp.callback_query(PhotoStates.waiting_for_style, F.data == "skip_style")
async def skip_style(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(LOCALE[lang]["skip_style"])
    await state.set_state(PhotoStates.waiting_for_photo)
    await callback.answer()

@dp.callback_query(PhotoStates.waiting_for_style, F.data == "all_styles")
async def show_all_styles(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(LOCALE[lang]["choose_style"], reply_markup=get_style_keyboard(lang))
    await callback.answer()

@dp.callback_query(PhotoStates.waiting_for_style, F.data.startswith("style_"))
async def process_style(callback: CallbackQuery, state: FSMContext):
    global remaining_generations
    chosen = callback.data
    style_info = FILM_PROMPTS.get(chosen, "универсальный стиль")
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]

    if GENERATION_LIMIT > 0 and remaining_generations <= 0:
        await callback.message.edit_text("Ох... Мой ИИ-аккумулятор сел...")
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.answer()
        return

    await callback.message.edit_text(loc["style_processing"])
    await bot.send_chat_action(callback.message.chat.id, "typing")

    b64_image = data.get("b64_image")
    if not b64_image:
        await callback.message.edit_text("😿 Фото потерялось из памяти.")
        await state.set_state(PhotoStates.waiting_for_photo)
        return

    try:
        prompt = BASE_PROMPT.format(style_info=style_info)
        ai_text = await ask_yandex(prompt, max_tokens="2000", temperature=0.6)
        if GENERATION_LIMIT > 0:
            remaining_generations -= 1

        xml_match = re.search(r"```xml\s*(.*?)\s*```", ai_text, re.DOTALL)
        if xml_match:
            xml_content = xml_match.group(1).strip()
            clean_text = ai_text.replace(xml_match.group(0), "").strip()
            if clean_text:
                await callback.message.answer(clean_text)
            preset_file = BufferedInputFile(xml_content.encode(), filename=f"{chosen}.xmp")
            await callback.message.answer_document(preset_file, caption=loc["preset_caption"])
        else:
            await callback.message.answer(ai_text)

        await state.set_state(PhotoStates.waiting_for_qa)
        await callback.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    except Exception as e:
        logger.error(f"Style processing error: {e}")
        await callback.message.edit_text("😿 Не получилось создать пресет.")
        await state.set_state(PhotoStates.waiting_for_style)
    await callback.answer()

# ---------- Q&A ----------
@dp.callback_query(PhotoStates.waiting_for_qa, F.data.startswith("qa_"))
async def process_qa(callback: CallbackQuery, state: FSMContext):
    qa = callback.data
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    if qa == "qa_done":
        await callback.message.edit_text(loc["qa_done"])
        await state.set_state(PhotoStates.waiting_for_photo)
    elif qa == "new_analysis":
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.message.edit_text("🦊 Жду новый кадр! Присылай фото.")
    else:
        answers = {
            "qa_wb": "🌡️ Баланс белого: поправь ползунок Temp в Lightroom, для улицы 5500-6500K.",
            "qa_sky": "⛅ Пересветы спасаем: Highlights вниз, градиентный фильтр на небе.",
            "qa_shadows": "🌑 Тени: Shadows вправо, но осторожно с шумами.",
            "qa_crop": "📐 Кадрирование: правило третей, объект на пересечении линий.",
            "qa_face": "Так-так-так... 👀 Сканирую, сканирую — а где же тут ты? ..."
        }
        await callback.message.answer(answers.get(qa, "🦊 Анализирую..."))
        await callback.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    await callback.answer()

# ---------- Генератор изображений ----------
@dp.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    prompt = message.text.replace("/generate", "", 1).strip()
    if not prompt:
        await state.set_state(PhotoStates.waiting_for_prompt)
        await message.answer(loc["generate_prompt"])
        return
    await bot.send_chat_action(message.chat.id, "upload_photo")
    await message.answer(loc["generating"])
    image_bytes = await generate_image(prompt)
    if image_bytes:
        filename = f"generated_{message.from_user.id}.png"
        with open(filename, "wb") as f: f.write(image_bytes)
        photo = FSInputFile(filename)
        await message.answer_photo(photo, caption=loc["generated"])
        os.remove(filename)
        await message.answer(loc["menu"], reply_markup=get_main_menu_keyboard(lang))
    else:
        await message.answer(loc["generate_error"])

@dp.message(PhotoStates.waiting_for_prompt, F.text & ~F.text.startswith("/"))
async def handle_generation_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    prompt = message.text.strip()
    if len(prompt) < 3:
        await message.answer("🦊 Описание слишком короткое.")
        return
    await bot.send_chat_action(message.chat.id, "upload_photo")
    await message.answer(loc["generating"])
    image_bytes = await generate_image(prompt)
    if image_bytes:
        filename = f"generated_{message.from_user.id}.png"
        with open(filename, "wb") as f: f.write(image_bytes)
        photo = FSInputFile(filename)
        await message.answer_photo(photo, caption=loc["generated"])
        os.remove(filename)
        await state.set_state(PhotoStates.waiting_for_photo)
        await message.answer(loc["menu"], reply_markup=get_main_menu_keyboard(lang))
    else:
        await message.answer(loc["generate_error"])
        await state.set_state(PhotoStates.waiting_for_photo)

# ---------- Документы ----------
@dp.message(F.document)
async def handle_document(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await message.answer(LOCALE[lang]["document_error"])

# ---------- Голосовые сообщения ----------
@dp.message(F.voice)
async def voice_handler(message: Message, state: FSMContext):
    if not CHAT_ENABLED or not VOICE_ENABLED:
        return
    await save_user(message.from_user.id)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    lang_code = "ru-RU" if lang == "ru" else "en-US"

    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    audio_bytes = file_bytes.read()

    await bot.send_chat_action(message.chat.id, "typing")
    text = await recognize_speech(audio_bytes, lang_code)
    if not text:
        await message.answer(loc["voice_unrecognized"])
        return

    analysis_keywords = ["проанализируй", "разбери фото", "оцени фото", "что с фото",
                         "проверь снимок", "скажи про фотку", "анализ", "дай совет по фото"]
    if any(word in text.lower() for word in analysis_keywords):
        await message.answer(loc["voice_analysis_request"])
        return

    reply_text = await ask_ari(text)
    corrected_reply = fix_ari_pronunciation(reply_text)
    voice_bytes = await synthesize_speech(corrected_reply, lang_code)
    if voice_bytes:
        voice_file = BufferedInputFile(voice_bytes, filename="ari_voice.ogg")
        await message.answer_voice(voice_file)
    await message.answer(reply_text)

# ---------- Текстовый чат ----------
@dp.message(F.text & ~F.text.startswith("/"))
async def smart_chat(message: Message, state: FSMContext):
    if not CHAT_ENABLED: return
    await save_user(message.from_user.id)
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]

    analysis_keywords = ["проанализируй", "разбери фото", "оцени фото", "что с фото",
                         "проверь снимок", "скажи про фотку", "анализ", "дай совет по фото"]
    if any(word in message.text.lower() for word in analysis_keywords):
        await message.answer(loc["ask_for_photo"])
        return

    current_state = await state.get_state()
    if current_state == PhotoStates.waiting_for_photo:
        await bot.send_chat_action(message.chat.id, "typing")
        await asyncio.sleep(random.uniform(0.5, 2.0))
        reply = await ask_ari(message.text)
        await message.answer(reply)
        return

    if current_state is not None:
        return

    if any(phrase in message.text.lower() for phrase in ["что ты умеешь", "что умеешь", "что можешь"]):
        await message.answer(loc["what"], reply_markup=get_main_menu_keyboard(lang))
        return
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(random.uniform(0.5, 2.0))
    reply = await ask_ari(message.text)
    await message.answer(reply)

# ---------- Стикеры ----------
@dp.message(F.sticker)
async def handle_sticker(message: Message):
    replies = [
        "🦊 Ой, какой классный стикер! Мой объектив улыбается.",
        "📸 Ха, чувствую лисье настроение!",
        "✨ Стикерам привет, но лучше пришли фото!"
    ]
    await message.answer(random.choice(replies))

# ---------- Заглушка для занятых состояний ----------
@dp.message(PhotoStates.waiting_for_style)
@dp.message(PhotoStates.waiting_for_qa)
async def text_in_busy_state(message: Message):
    await message.answer("🦊 Я сейчас занята обработкой фото. Выбери кнопку или дождись завершения.")

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
