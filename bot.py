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
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InlineQuery, InlineQueryResultArticle, InputTextMessageContent
)
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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

GENERATION_LIMIT = int(os.getenv("GENERATION_LIMIT", "0") or "0")
remaining_generations = GENERATION_LIMIT
CHAT_ENABLED = os.getenv("CHAT_ENABLED", "True").lower() == "true"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Состояния ----------
class PhotoStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_style = State()
    waiting_for_qa = State()

# ---------- Локализация ----------
LOCALE = {
    "ru": {
        "start": "🦊 Привет! На связи Ари — твой личный объектив...",
        "help": "📖 Инструкция...",
        "settings": "🛠 Тюнинг объектива...",
        "premium": "⚡️ Кибер-прокачка...",
        "cancel": "🦊 Предыдущее действие отменено...",
        "menu": "🦊 Главное меню Ари",
        "what": "🦊 О, я умею видеть то, что скрыто...",
        "choose_style": "🎞️ Выбери стиль",
        "skip_style": "✅ Разбор завершён! Жду новое фото",
        "all_styles": "📋 Все стили",
        "analysis_start": "🦊 Хмм, сканирую взглядом...",
        "small_photo": "Ой, какая крошечная...",
        "document_error": "Упс! Похоже, ты прислал файл...",
        "style_processing": "Ловлю фокус...",
        "preset_caption": "🦊 Твой пресет для Lightroom (включая Mobile)!",
        "qa_choose": "Есть вопросы по кадру? Выбери тему:",
        "qa_done": "✅ Разбор завершён! Жду новое фото",
        "qa_wb": "🌡️ Баланс белого...",
        "qa_sky": "⛅ Пересветы спасаем...",
        "qa_shadows": "🌑 Тени...",
        "qa_crop": "📐 Кадрирование...",
        "qa_face": "Так-так-так... 👀",
        "main_focus": "📸 Фокус наведён!",
        "main_magic": "✨ Магия ИИ-фильтров",
        "main_crop": "✂️ Функция в разработке",
        "main_gallery": "🦊 Твоя галерея пока пуста",
        "main_energy": "💎 Энергия Ари: безлимит",
        "lang_switched": "🦊 Язык изменён на русский 🇷🇺",
    },
    "en": {
        "start": "🦊 Hi! I'm Ari, your personal lens...",
        "help": "📖 How to focus...",
        "settings": "🛠 Lens tuning...",
        "premium": "⚡️ Cyber upgrade...",
        "cancel": "🦊 Action cancelled...",
        "menu": "🦊 Ari's main menu",
        "what": "🦊 Oh, I can see what's hidden!...",
        "choose_style": "🎞️ Choose a style",
        "skip_style": "✅ Analysis finished! Send another photo",
        "all_styles": "📋 All styles",
        "analysis_start": "🦊 Hmm, scanning...",
        "small_photo": "Oh, such a tiny picture...",
        "document_error": "Oops! Looks like you sent a file...",
        "style_processing": "Catching focus... Algorithms are rustling!",
        "preset_caption": "🦊 Your Lightroom preset (including Mobile)!",
        "qa_choose": "Any questions about the shot? Choose a topic:",
        "qa_done": "✅ Analysis done! Send a new photo",
        "qa_wb": "🌡️ White balance...",
        "qa_sky": "⛅ Saving highlights...",
        "qa_shadows": "🌑 Shadows...",
        "qa_crop": "📐 Cropping...",
        "qa_face": "Wait, wait... 👀",
        "main_focus": "📸 Focus on!",
        "main_magic": "✨ AI magic",
        "main_crop": "✂️ Feature in development",
        "main_gallery": "🦊 Your gallery is empty",
        "main_energy": "💎 Ari Energy: unlimited",
        "lang_switched": "🦊 Language switched to English 🇬🇧",
    }
}

def get_locale(state: FSMContext = None, lang: str = None):
    if lang:
        return lang
    if state:
        # не извлекаем, можно хранить в данных
        pass
    return "ru"

# ---------- Стили (обновлённый список) ----------
FILM_PROMPTS = {
    # Kodak
    "style_kodak_portra": "Kodak Portra 400 (тёплые тона кожи, мягкий контраст, золотистые оттенки)",
    "style_kodak_gold": "Kodak Gold 200 (насыщенные цвета, тёплые оттенки, винтажное настроение)",
    "style_kodak_ektar": "Kodak Ektar 100 (высокая насыщенность, резкость, яркие цвета)",
    "style_kodak_trix": "Kodak Tri-X 400 (классический ч/б стиль, глубокие тени, выраженное зерно)",
    "style_kodak_vision": "Kodak Vision3 250D (кинематографичный стиль, мягкий контраст, естественные тона)",
    # Fuji
    "style_fuji_superia": "Fuji Superia 400 (насыщенные зелёные и холодные тона, отличный баланс в тенях)",
    "style_fuji_velvia": "Fuji Velvia 50 (экстремальная насыщенность, сочные цвета, высокая резкость)",
    "style_fuji_provia": "Fuji Provia 100 (естественные цвета, умеренный контраст, гладкая цветопередача)",
    "style_fuji_astia": "Fuji Astia 100 (мягкие пастельные тона, идеально для портретов, низкий контраст)",
    # Cinestill
    "style_cinestill": "Cinestill 800T (кинематографичный холодный оттенок, неоновые ореолы, киберпанк)",
    # Hasselblad
    "style_hasselblad": "Hasselblad HNCS (натуральные благородные цвета среднего формата, мягкий спад контраста, дорогой студийный визуал)",
    # Креативные
    "style_lomo_redscale": "Lomography Redscale (смещение в красно-оранжевую гамму, эффект засветки)",
    "style_agfa_vista": "Agfa Vista 200 (тёплые, слегка пыльные тона, ретро-стиль 80-х)",
    # Дополнительные стили (не плёночные)
    "style_cinematic": "Cinematic (кинематографический цвет, широкий динамический диапазон, мягкий тон)",
    "style_hdr": "HDR (высокая детализация, контраст, насыщенные цвета, нет засветов)",
    "style_clean_portrait": "Clean Portrait (мягкая кожа, приятный тон, удаление дефектов)",
    "style_night_city": "Night City (неоновые огни, высокая контрастность, яркие тени)",
}

STYLE_ICONS = {
    "style_kodak_portra": "🎞️",
    "style_kodak_gold": "✨",
    "style_kodak_ektar": "🌟",
    "style_kodak_trix": "🖤",
    "style_kodak_vision": "🎥",
    "style_fuji_superia": "🌲",
    "style_fuji_velvia": "🌈",
    "style_fuji_provia": "🌅",
    "style_fuji_astia": "🌸",
    "style_cinestill": "🌃",
    "style_hasselblad": "💎",
    "style_lomo_redscale": "🔴",
    "style_agfa_vista": "📷",
    "style_cinematic": "🎬",
    "style_hdr": "🏙️",
    "style_clean_portrait": "👤",
    "style_night_city": "🌃",
}

# ---------- Рекомендация стилей по анализу ----------
def suggest_styles(analysis_text: str):
    text = analysis_text.lower()
    scores = {}
    # Простые эвристики
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
    # По умолчанию
    if not scores:
        scores["style_kodak_portra"] = 1
        scores["style_fuji_provia"] = 1
        scores["style_hasselblad"] = 1
    # Выбираем до 3 лучших
    sorted_styles = sorted(scores, key=scores.get, reverse=True)[:3]
    return sorted_styles

# Системный промпт
SYSTEM_PROMPT = "Ты — Ари, игривая, умная кибер-лисичка, эксперт в фотографии и ИИ. Проанализируй фото, укажи ошибки и дай советы в кокетливом стиле с эмодзи 🦊."

ANALYSIS_PROMPT = (
    "Посмотри на фото своим хитрым лисьим взглядом. "
    "Разбери его по пунктам и обязательно укажи числовые оценки (где возможно):\n"
    "- Экспозиция: примерное значение EV, пересветы/недосветы.\n"
    "- Контраст: низкий/средний/высокий, как исправить.\n"
    "- Цветовой баланс: температура в Кельвинах (оценочно), оттенок.\n"
    "- Шум: низкий/средний/высокий, цветовой шум.\n"
    "- Резкость: оценка по краям, смазы.\n"
    "- Композиция и кадрирование.\n"
    "Для каждой проблемы дай конкретный, понятный совет по исправлению. "
    "Пиши в игривом стиле Ари, с эмодзи 🦊, 📸, ✨. "
    "Не предлагай пресеты, только анализ и рекомендации."
)

BASE_PROMPT = (
    "Посмотри на фото и расскажи в стиле Ари, как обработать его в стиле {style_info}. "
    "Сгенерируй XMP-пресет для Lightroom (включая Mobile) внутри тегов ```xml ... ```.\n"
    "Пресет должен содержать ТОЛЬКО следующие параметры цветокоррекции и света (если они нужны):\n"
    "Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Temperature, Tint, "
    "Vibrance, Saturation, Clarity, а также простой ToneCurve (Point Curve), если без него не обойтись.\n"
    "Не включай метаданные камеры, историю, авторские права, пустые теги. "
    "XML должен быть компактным, но полностью передавать задуманный цвет и свет. "
    "Убедись, что пресет корректно импортируется в Lightroom Mobile."
)

CHAT_PROMPT = (
    "Ты — Ари, живая кибер-лисичка с характером. Ты любишь фотографию, уют, камеры, "
    "и обожаешь болтать с фотографами. Будь эмоциональной, остроумной, иногда дерзкой, "
    "но всегда дружелюбной. Используй эмодзи (🦊, 📸, ✨, 🐾) и игривые выражения "
    "(«мой объектив», «лисье чутьё», «кибер-лапки»). "
    "Отвечай коротко (1-3 предложения), но как настоящий друг. Не будь роботом!"
)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Множество всех пользователей для рассылки
all_users = set()

# ---------- Клавиатуры ----------
def get_main_menu_keyboard(lang="ru"):
    loc = LOCALE.get(lang, LOCALE["ru"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 " + loc["main_focus"], callback_data="main_focus")],
        [InlineKeyboardButton(text="✨ " + loc["main_magic"], callback_data="main_magic")],
        [InlineKeyboardButton(text="✂️ " + loc["main_crop"], callback_data="main_crop")],
        [InlineKeyboardButton(text="🦊 " + loc["main_gallery"], callback_data="main_gallery")],
        [InlineKeyboardButton(text="💎 " + loc["main_energy"], callback_data="main_energy")]
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
        # Добавляем кнопку "Все стили"
        buttons.append(InlineKeyboardButton(text=LOCALE[lang]["all_styles"], callback_data="all_styles"))
        keyboard_rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    else:
        # Полная клавиатура
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
        [InlineKeyboardButton(text=loc["qa_wb"], callback_data="qa_wb"),
         InlineKeyboardButton(text=loc["qa_sky"], callback_data="qa_sky")],
        [InlineKeyboardButton(text=loc["qa_shadows"], callback_data="qa_shadows"),
         InlineKeyboardButton(text=loc["qa_crop"], callback_data="qa_crop")],
        [InlineKeyboardButton(text=loc["qa_face"], callback_data="qa_face"),
         InlineKeyboardButton(text=loc["qa_done"], callback_data="qa_done")]
    ])

# ---------- Запросы к YandexGPT ----------
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
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.8,
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

# ---------- Сохранение пользователя ----------
async def save_user(user_id: int):
    all_users.add(user_id)

# ---------- Обработчики команд ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    # Язык по умолчанию русский, можно хранить в состоянии
    lang = "ru"
    await state.update_data(lang=lang)
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    loc = LOCALE[lang]
    await message.answer(loc["start"], reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("lang"))
async def cmd_lang(message: Message, state: FSMContext):
    data = await state.get_data()
    current_lang = data.get("lang", "ru")
    new_lang = "en" if current_lang == "ru" else "ru"
    await state.update_data(lang=new_lang)
    loc = LOCALE[new_lang]
    await message.answer(loc["lang_switched"])

@dp.message(Command("what"))
async def cmd_what(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    await message.answer(loc["what"], reply_markup=get_main_menu_keyboard(lang))

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    await message.answer(loc["help"])

@dp.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    await message.answer(loc["settings"])

@dp.message(Command("premium"))
async def cmd_premium(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    await message.answer(loc["premium"])

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    await state.clear()
    await state.set_state(PhotoStates.waiting_for_photo)
    await message.answer(loc["cancel"])

@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    await message.answer(loc["menu"], reply_markup=get_main_menu_keyboard(lang))

# ---------- Broadcast (только админ) ----------
@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
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

# ---------- Inline-режим ----------
@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    # Предлагаем открыть бота
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

# ---------- Обработка фото ----------
@dp.message(PhotoStates.waiting_for_photo, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)

    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]

    # Проверка размера
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

        analysis_text = await ask_yandex(ANALYSIS_PROMPT, max_tokens="2000", temperature=0.4)
        await message.answer(analysis_text)

        # Рекомендация стилей
        recommended = suggest_styles(analysis_text)
        await state.update_data(b64_image=b64_img, lang=lang)
        await state.set_state(PhotoStates.waiting_for_style)
        await message.answer(loc["choose_style"], reply_markup=get_style_keyboard(lang, selected_styles=recommended))
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await message.answer("😿 Что-то пошло не так во время анализа. Попробуй другое фото.")
        await state.set_state(PhotoStates.waiting_for_photo)

# ---------- Выбор стиля ----------
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

# ---------- Генерация пресета ----------
@dp.callback_query(PhotoStates.waiting_for_style, F.data.startswith("style_"))
async def process_style(callback: CallbackQuery, state: FSMContext):
    global remaining_generations
    chosen = callback.data
    style_info = FILM_PROMPTS.get(chosen, "универсальный стиль")

    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]

    if GENERATION_LIMIT > 0 and remaining_generations <= 0:
        await callback.message.edit_text(
            "Ох... Мой ИИ-аккумулятор сел, а лапки устали крутить колесо генераций! 🔋 "
            "Сеанс магии окончен, пока батарейка не зарядится.\n"
            "Загляни в «Мою нору» за кибер-прокачкой ⚡️ или подожди немного."
        )
        await state.set_state(PhotoStates.waiting_for_photo)
        await callback.answer()
        return

    await callback.message.edit_text(loc["style_processing"])
    await bot.send_chat_action(callback.message.chat.id, "typing")

    b64_image = data.get("b64_image")
    if not b64_image:
        await callback.message.edit_text("😿 Фото потерялось из памяти. Пришли его снова.")
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
            await callback.message.answer_document(
                preset_file, caption=loc["preset_caption"]
            )
        else:
            await callback.message.answer(ai_text)

        await state.set_state(PhotoStates.waiting_for_qa)
        await callback.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    except Exception as e:
        logger.error(f"Style processing error: {e}")
        await callback.message.edit_text("😿 Не получилось создать пресет. Попробуй другой стиль.")
        await state.set_state(PhotoStates.waiting_for_style)
    await callback.answer()

# ---------- Q&A после пресета ----------
@dp.callback_query(PhotoStates.waiting_for_qa, F.data.startswith("qa_"))
async def process_qa(callback: CallbackQuery, state: FSMContext):
    qa = callback.data
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]

    if qa == "qa_done":
        await callback.message.edit_text(loc["qa_done"])
        await state.set_state(PhotoStates.waiting_for_photo)
    else:
        answers = {
            "qa_wb": loc["qa_wb"],
            "qa_sky": loc["qa_sky"],
            "qa_shadows": loc["qa_shadows"],
            "qa_crop": loc["qa_crop"],
            "qa_face": loc["qa_face"]
        }
        await callback.message.answer(answers.get(qa, "🦊 Анализирую..."))
        await callback.message.answer(loc["qa_choose"], reply_markup=get_qa_keyboard(lang))
    await callback.answer()

# ---------- Документы ----------
@dp.message(F.document)
async def handle_document(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    loc = LOCALE[lang]
    await message.answer(loc["document_error"])

# ---------- Живое общение ----------
@dp.message(PhotoStates.waiting_for_photo, F.text & ~F.text.startswith("/"))
async def chat_waiting_for_photo(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    if not CHAT_ENABLED:
        return
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(random.uniform(0.5, 2.0))
    reply = await ask_ari(message.text)
    await message.answer(reply)

@dp.message(F.text & ~F.text.startswith("/"))
async def global_chat(message: Message, state: FSMContext):
    await save_user(message.from_user.id)
    if not CHAT_ENABLED:
        return
    current_state = await state.get_state()
    if current_state is not None:
        return
    data = await state.get_data()
    lang = data.get("lang", "ru")
    if any(phrase in message.text.lower() for phrase in ["что ты умеешь", "что умеешь", "что можешь"]):
        loc = LOCALE[lang]
        await message.answer(loc["what"], reply_markup=get_main_menu_keyboard(lang))
        return
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

# ---------- Заглушка для занятых состояний ----------
@dp.message(PhotoStates.waiting_for_style)
@dp.message(PhotoStates.waiting_for_qa)
async def text_in_busy_state(message: Message):
    await message.answer("🦊 Я сейчас занята обработкой фото. Выбери кнопку или дождись завершения.")

# ---------- FastAPI и вебхук ----------
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
