import os
import time
import logging
import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import aiofiles
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from logging.handlers import RotatingFileHandler
from config import Config
from utils import (
    safe_read_file, safe_write_file, safe_append_file,
    cleanup_temp_files, check_disk_space, check_file_size,
    sanitize_filename, sanitize_input, generate_secure_id,
    validate_text_length, validate_questions_count, check_rate_limit
)

# Настройка логирования с ротацией
log_handler = RotatingFileHandler(
    'bot.log',
    maxBytes=1024*1024,  # 1MB
    backupCount=5,
    encoding=Config.FILE_ENCODING
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        log_handler,
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=Config.API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Проверка конфигурации при запуске
Config.validate_config()

# Используем текущую директорию бота
bot_dir = os.path.dirname(os.path.abspath(__file__))
SURVEYS_DIR = os.path.join(bot_dir, "surveys")
os.makedirs(SURVEYS_DIR, exist_ok=True)
SURVEYS_FILE = os.path.join(SURVEYS_DIR, "surveys.txt")
ACTIVE_SURVEY_FILE = os.path.join(SURVEYS_DIR, "active_survey.txt")
DATA_FILE = os.path.join(bot_dir, "user_data.txt")

# Глобальный словарь для rate limiting
RATE_LIMIT: Dict[int, Dict[str, Any]] = {}

def check_rate_limit(user_id: int, action_type: str = "default", is_admin: bool = False) -> bool:
    """
    Улучшенная проверка rate limiting с разными ограничениями для разных действий
    """
    current_time = datetime.now()
    if user_id not in RATE_LIMIT:
        RATE_LIMIT[user_id] = {}
    
    if action_type not in RATE_LIMIT[user_id]:
        RATE_LIMIT[user_id][action_type] = {
            "last_request": current_time,
            "request_count": 1
        }
        return True

    user_limits = RATE_LIMIT[user_id][action_type]
    time_diff = (current_time - user_limits["last_request"]).total_seconds()
    
    # Разные ограничения для разных типов действий
    if is_admin:
        max_requests = Config.ADMIN_RATE_LIMIT_MAX_REQUESTS
        time_window = Config.ADMIN_RATE_LIMIT_WINDOW
    else:
        max_requests = Config.RATE_LIMIT_MAX_REQUESTS
        time_window = Config.RATE_LIMIT_WINDOW
    
    if time_diff < time_window:
        if user_limits["request_count"] >= max_requests:
            return False
        user_limits["request_count"] += 1
    else:
        user_limits["last_request"] = current_time
        user_limits["request_count"] = 1
    
    return True

async def cleanup_task():
    """
    Периодическая очистка временных файлов и устаревших rate limit записей
    """
    while True:
        try:
            await cleanup_temp_files()
            
            # Очистка устаревших rate limit записей
            current_time = datetime.now()
            for user_id in list(RATE_LIMIT.keys()):
                for action_type in list(RATE_LIMIT[user_id].keys()):
                    last_request = RATE_LIMIT[user_id][action_type]["last_request"]
                    if (current_time - last_request).total_seconds() > Config.RATE_LIMIT_CLEANUP_TIME:
                        del RATE_LIMIT[user_id][action_type]
                if not RATE_LIMIT[user_id]:
                    del RATE_LIMIT[user_id]
                    
            await asyncio.sleep(Config.CLEANUP_INTERVAL)
        except Exception as e:
            logger.error(f"Ошибка в cleanup_task: {e}")
            await asyncio.sleep(Config.ERROR_RETRY_INTERVAL)

class SurveyState(StatesGroup):
    waiting_for_answer = State()

class CreateSurveyState(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_questions = State()
    waiting_for_answers = State()

class EditSurveyState(StatesGroup):
    waiting_for_edited_question = State()
    waiting_for_question_number = State()
    waiting_for_new_question = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

class Survey:
    def __init__(self, name: str, description: str, questions: List[str], answers: Optional[List[List[str]]] = None, survey_id: Optional[str] = None):
        if not validate_text_length(name, Config.MAX_SURVEY_NAME_LENGTH, Config.MIN_SURVEY_NAME_LENGTH):
            raise ValueError(f"Название опроса должно быть от {Config.MIN_SURVEY_NAME_LENGTH} до {Config.MAX_SURVEY_NAME_LENGTH} символов")
        if not validate_text_length(description, Config.MAX_SURVEY_DESCRIPTION_LENGTH, Config.MIN_SURVEY_DESCRIPTION_LENGTH):
            raise ValueError(f"Описание опроса должно быть от {Config.MIN_SURVEY_DESCRIPTION_LENGTH} до {Config.MAX_SURVEY_DESCRIPTION_LENGTH} символов")
        if not validate_questions_count(questions):
            raise ValueError(f"Количество вопросов должно быть от {Config.MIN_QUESTIONS} до {Config.MAX_QUESTIONS}")
        
        self.name = sanitize_input(name)
        self.description = sanitize_input(description)
        self.questions = [sanitize_input(q) for q in questions]
        self.survey_id = survey_id or generate_secure_id()
        
        # Инициализация ответов
        self.answers = []
        if answers and len(answers) > 0:
            # Если переданы пользовательские ответы, используем их
            for answer_set in answers:
                if isinstance(answer_set, list):
                    self.answers.append([sanitize_input(a) for a in answer_set])
                else:
                    self.answers.append(["Да", "Нет", "Свой ответ"])
        else:
            # Если ответы не переданы, создаем стандартные для каждого вопроса
            self.answers = [["Да", "Нет", "Свой ответ"] for _ in questions]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "questions": self.questions,
            "answers": self.answers,
            "survey_id": self.survey_id
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Survey':
        return cls(
            name=data["name"],
            description=data["description"],
            questions=data["questions"],
            answers=data.get("answers"),
            survey_id=data["survey_id"]
        )

async def load_surveys() -> Dict[str, Survey]:
    """Загрузка опросов из файла"""
    try:
        if not os.path.exists(SURVEYS_FILE):
            logger.info("Файл с опросами не существует, возвращаем пустой словарь")
            return {}

        content = await safe_read_file(SURVEYS_FILE)
        if not content or not content.strip():
            logger.info("Файл с опросами пуст, возвращаем пустой словарь")
            return {}

        surveys = {}
        try:
            surveys_data = json.loads(content)
            logger.info(f"Загружены данные опросов: {surveys_data}")
            
            if not isinstance(surveys_data, dict):
                logger.error(f"Некорректный формат данных опросов: {type(surveys_data)}")
                return {}

            for survey_id, data in surveys_data.items():
                try:
                    # Проверяем наличие всех необходимых полей
                    required_fields = ["name", "description", "questions"]
                    if not all(field in data for field in required_fields):
                        missing_fields = [field for field in required_fields if field not in data]
                        logger.error(f"Отсутствуют обязательные поля в данных опроса {survey_id}: {missing_fields}")
                        continue

                    # Проверяем корректность answers
                    answers = data.get("answers", [])
                    if not isinstance(answers, list):
                        logger.warning(f"Некорректный формат answers для опроса {survey_id}, использую пустой список")
                        answers = []

                    # Создаем объект Survey
                    survey = Survey(
                        name=str(data["name"]),
                        description=str(data["description"]),
                        questions=[str(q) for q in data["questions"]],
                        answers=[[str(a) for a in ans] if isinstance(ans, list) else ["Да", "Нет", "Свой ответ"] for ans in answers],
                        survey_id=str(data.get("survey_id", survey_id))
                    )
                    surveys[survey_id] = survey
                    logger.info(f"Успешно загружен опрос {survey_id}: {survey.name}")
                except Exception as e:
                    logger.error(f"Ошибка при создании опроса {survey_id}: {e}")
                    continue

            return surveys

        except json.JSONDecodeError as e:
            logger.error(f"Ошибка при декодировании JSON: {e}, содержимое файла: {content[:200]}")
            return {}

    except Exception as e:
        logger.error(f"Ошибка при загрузке опросов: {e}")
        return {}

async def save_surveys(surveys: Dict[str, Survey]) -> None:
    try:
        surveys_data = {}
        for survey_id, survey in surveys.items():
            surveys_data[survey_id] = {
                "survey_id": survey.survey_id,
                "name": survey.name,
                "description": survey.description,
                "questions": survey.questions,
                "answers": survey.answers
            }
        
        json_str = json.dumps(surveys_data, ensure_ascii=False, indent=2)
        await safe_write_file(SURVEYS_FILE, json_str)
    except Exception as e:
        logger.error(f"Ошибка при сохранении опросов: {e}")
        raise

async def get_active_survey_id() -> Optional[str]:
    try:
        content = await safe_read_file(ACTIVE_SURVEY_FILE)
        return content.strip() if content else None
    except Exception as e:
        logger.error(f"Ошибка при получении активного опроса: {e}")
        return None

async def set_active_survey(survey_id: str) -> None:
    try:
        await safe_write_file(ACTIVE_SURVEY_FILE, survey_id)
    except Exception as e:
        logger.error(f"Ошибка при установке активного опроса: {e}")

def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id in Config.ADMIN_IDS

async def rate_limit_handler(message: Message, is_admin: bool = False) -> bool:
    """Обработчик rate limiting с уведомлением пользователя"""
    if not check_rate_limit(message.from_user.id, "default", is_admin):
        await message.answer("⚠️ Пожалуйста, подождите немного перед следующей попыткой.")
        return False
    return True

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    try:
        user_id = message.from_user.id
        is_admin_user = is_admin(user_id)
        
        if not await rate_limit_handler(message, is_admin_user):
            return

        logger.info(f"Пользователь {user_id} запустил бота")

        surveys = await load_surveys()
        if not surveys:
            if is_admin_user:
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="Админ-панель 👨‍💻", callback_data="admin")
                    ]]
                )
                await message.answer(
                    "👋 Привет! В данный момент нет доступных опросов.\n"
                    "Вы можете создать новый опрос через админ-панель.",
                    reply_markup=keyboard
                )
            else:
                await message.answer(
                    "👋 Привет! В данный момент нет доступных опросов.\n"
                    "Пожалуйста, попробуйте позже."
                )
            return

        # Создаем кнопки для всех опросов
        buttons = []
        active_survey_id = await get_active_survey_id()
        
        for survey_id, survey in surveys.items():
            # Добавляем метку активного опроса
            status = "✨ " if survey_id == active_survey_id else ""
            buttons.append([
                InlineKeyboardButton(
                    text=f"{status}{survey.name}",
                    callback_data=f"select_survey_{survey_id}"
                )
            ])

        # Добавляем кнопку админ-панели для администраторов
        if is_admin_user:
            buttons.append([
                InlineKeyboardButton(text="Админ-панель 👨‍💻", callback_data="admin")
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await message.answer(
            "👋 Привет! Выберите опрос для прохождения:\n\n"
            "✨ - отмечен рекомендуемый опрос",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")

@dp.callback_query(F.data.startswith("select_survey_"))
async def process_select_survey(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        survey_id = callback.data.replace("select_survey_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден или больше не доступен.")
            await callback.answer()
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="Начать опрос 📝", callback_data=f"start_survey_{survey_id}"),
                InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_surveys")
            ]]
        )
        
        message_text = (
            f"📋 {survey.name}\n\n"
            f"📝 Описание: {survey.description}\n"
            f"❓ Количество вопросов: {len(survey.questions)}\n\n"
            "Нажмите кнопку ниже, чтобы начать опрос:"
        )
        
        try:
            await callback.message.edit_text(message_text, reply_markup=keyboard)
        except Exception as edit_error:
            logger.warning(f"Не удалось отредактировать сообщение: {edit_error}")
            await callback.message.answer(message_text, reply_markup=keyboard)
            try:
                await callback.message.delete()
            except Exception as delete_error:
                logger.warning(f"Не удалось удалить старое сообщение: {delete_error}")
    except Exception as e:
        logger.error(f"Ошибка при выборе опроса: {e}")
        await callback.message.answer(
            "Произошла ошибка. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_surveys")
async def process_back_to_surveys(callback: CallbackQuery) -> None:
    try:
        # Создаем новое сообщение вместо модификации существующего
        surveys = await load_surveys()
        if not surveys:
            if is_admin(callback.from_user.id):
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="Админ-панель 👨‍💻", callback_data="admin")
                    ]]
                )
                await callback.message.answer(
                    "👋 В данный момент нет доступных опросов.\n"
                    "Вы можете создать новый опрос через админ-панель.",
                    reply_markup=keyboard
                )
            else:
                await callback.message.answer(
                    "👋 В данный момент нет доступных опросов.\n"
                    "Пожалуйста, попробуйте позже."
                )
        else:
            buttons = []
            active_survey_id = await get_active_survey_id()
            
            for survey_id, survey in surveys.items():
                status = "✨ " if survey_id == active_survey_id else ""
                buttons.append([
                    InlineKeyboardButton(
                        text=f"{status}{survey.name}",
                        callback_data=f"select_survey_{survey_id}"
                    )
                ])

            if is_admin(callback.from_user.id):
                buttons.append([
                    InlineKeyboardButton(text="Админ-панель 👨‍💻", callback_data="admin")
                ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            await callback.message.answer(
                "👋 Выберите опрос для прохождения:\n\n"
                "✨ - отмечен рекомендуемый опрос",
                reply_markup=keyboard
            )
        
        try:
            await callback.message.delete()
        except:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка при возврате к списку опросов: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

async def check_completed_survey(user_id: int, survey_id: str) -> bool:
    """Проверяет, завершил ли пользователь опрос"""
    try:
        if not os.path.exists(Config.DATA_FILE):
            return False
            
        content = await safe_read_file(Config.DATA_FILE)
        if not content:
            return False

        lines = content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if not line:
                i += 1
                continue
                
            if "👤 Пользователь:" in line:
                i += 1
                if i < len(lines):
                    id_line = lines[i].strip()
                    if "🆔 ID:" in id_line:
                        user_survey = f"{user_id}_{survey_id}"
                        if user_survey in id_line:
                            # Ищем маркер завершения опроса
                            while i < len(lines):
                                if "✅ Опрос завершён" in lines[i]:
                                    return True
                                elif "━━━━━━━━━━━━━━━━━━━━━━" in lines[i]:
                                    break
                                i += 1
            i += 1
                
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке завершенных опросов: {e}")
        return False

@dp.callback_query(F.data.startswith("start_survey_"))
async def process_start_selected_survey(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        survey_id = callback.data.replace("start_survey_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден или больше не доступен.")
            await callback.answer()
            return

        # Проверяем, активен ли опрос
        active_survey_id = await get_active_survey_id()
        if active_survey_id and survey_id != active_survey_id and not is_admin(callback.from_user.id):
            await callback.message.answer(
                "⚠️ Этот опрос в данный момент не активен.\n"
                "Пожалуйста, выберите активный опрос (отмечен звездочкой ✨)"
            )
            await callback.answer()
            return

        # Проверяем, не проходил ли пользователь этот опрос ранее
        if await check_completed_survey(callback.from_user.id, survey_id):
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться к списку опросов", callback_data="back_to_surveys")
                ]]
            )
            await callback.message.answer(
                "⚠️ Вы уже проходили этот опрос.\n"
                "Пожалуйста, выберите другой опрос из списка.",
                reply_markup=keyboard
            )
            try:
                await callback.message.delete()
            except Exception as delete_error:
                logger.warning(f"Не удалось удалить сообщение: {delete_error}")
            await callback.answer()
            return

        await state.update_data(
            current_question=0,
            survey_id=survey_id,
            answers=[]
        )

        # Создаем reply-клавиатуру с вариантами ответов для текущего вопроса
        answer_buttons = []
        if hasattr(survey, 'answers') and survey.answers and len(survey.answers) > 0:
            current_answers = survey.answers[0]
            for answer in current_answers:
                if answer == "Свой ответ":
                    answer_buttons.append([KeyboardButton(text="✍️ Свой ответ")])
                else:
                    answer_buttons.append([KeyboardButton(text=answer)])
        else:
            answer_buttons = [
                [KeyboardButton(text="Да ✅")],
                [KeyboardButton(text="Нет ❌")],
                [KeyboardButton(text="✍️ Свой ответ")]
            ]

        keyboard = ReplyKeyboardMarkup(
            keyboard=answer_buttons,
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        # Отправляем новое сообщение с вопросом
        await callback.message.answer(
            f"Вопрос 1 из {len(survey.questions)}:\n\n{survey.questions[0]}",
            reply_markup=keyboard
        )
        
        # Пытаемся удалить предыдущее сообщение
        try:
            await callback.message.delete()
        except Exception as delete_error:
            logger.warning(f"Не удалось удалить сообщение: {delete_error}")
        
        await state.set_state(SurveyState.waiting_for_answer)
    except Exception as e:
        logger.error(f"Ошибка при начале опроса: {e}")
        await callback.message.answer(
            "Произошла ошибка при начале опроса. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()
    
    await callback.answer()

@dp.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав для доступа к админ-панели.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Создать новый опрос", callback_data="create_survey")],
            [InlineKeyboardButton(text="⚙️ Управление опросами", callback_data="manage_surveys")],
            [InlineKeyboardButton(text="📊 Просмотр статистики", callback_data="view_stats")],
            [InlineKeyboardButton(text="👥 Просмотр ответов", callback_data="view_user_answers")],
            [InlineKeyboardButton(text="📥 Скачать данные опросов", callback_data="download_data")],
            [InlineKeyboardButton(text="📢 Рассылка сообщения", callback_data="broadcast")]
        ]
    )
    await message.answer(
        "🎯 Панель администратора\n\n"
        "Выберите нужное действие из меню ниже:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "admin")
async def process_admin_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для доступа к админ-панели.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать новый опрос 📝", callback_data="create_survey")],
            [InlineKeyboardButton(text="Управление опросами 📊", callback_data="manage_surveys")],
            [InlineKeyboardButton(text="Просмотр статистики 📈", callback_data="view_stats")],
            [InlineKeyboardButton(text="Просмотр ответов пользователей 🔍", callback_data="view_user_answers")],
            [InlineKeyboardButton(text="Скачать данные опросов 📥", callback_data="download_data")],
            [InlineKeyboardButton(text="Рассылка сообщения 📢", callback_data="broadcast")]
        ]
    )
    await callback.message.delete()
    await callback.message.answer("👨‍💻 Добро пожаловать в админ-панель. Выберите действие:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "create_survey")
async def process_create_survey(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для создания опроса.", show_alert=True)
        return

    await callback.message.delete()
    await callback.message.answer(
        "Давайте создадим новый опрос!\n"
        "Введите название опроса:"
    )
    await state.set_state(CreateSurveyState.waiting_for_name)
    await callback.answer()

@dp.message(CreateSurveyState.waiting_for_name)
async def process_survey_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not name:
        await message.answer("Название не может быть пустым. Введите название опроса:")
        return

    await state.update_data(name=name)
    await message.answer(
        "Отлично! Теперь введите описание опроса.\n"
        "Это описание будут видеть пользователи перед началом опроса:"
    )
    await state.set_state(CreateSurveyState.waiting_for_description)

@dp.message(CreateSurveyState.waiting_for_description)
async def process_survey_description(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    if not description:
        await message.answer("Описание не может быть пустым. Введите описание опроса:")
        return

    await state.update_data(description=description)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово", callback_data="done_adding_questions")
        ]]
    )
    await message.answer(
        "Теперь введите вопросы для опроса.\n"
        "Отправляйте каждый вопрос отдельным сообщением.\n"
        "Когда закончите, нажмите кнопку 'Готово':",
        reply_markup=keyboard
    )
    await state.set_state(CreateSurveyState.waiting_for_questions)

@dp.message(CreateSurveyState.waiting_for_questions)
async def process_survey_question(message: Message, state: FSMContext) -> None:
    question = message.text.strip()
    if not question:
        await message.answer("Вопрос не может быть пустым. Введите вопрос:")
        return

    data = await state.get_data()
    questions = data.get("questions", [])
    answers = data.get("answers", [])
    
    if len(questions) >= Config.MAX_QUESTIONS:
        await message.answer(f"Достигнуто максимальное количество вопросов ({Config.MAX_QUESTIONS})")
        return
        
    questions.append(question)
    await state.update_data(questions=questions)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да/Нет + Свой ответ", callback_data="use_default_answers")],
            [InlineKeyboardButton(text="📝 Задать варианты ответов", callback_data="set_custom_answers")],
            [InlineKeyboardButton(text="✍️ Только свой ответ", callback_data="custom_answer_only")],
            [InlineKeyboardButton(text="✅ Завершить создание опроса", callback_data="done_adding_questions")]
        ]
    )
    await message.answer(
        f"✅ Вопрос #{len(questions)} добавлен.\n"
        "Выберите тип ответов для этого вопроса:\n"
        "1. Стандартные варианты (Да/Нет + возможность своего ответа)\n"
        "2. Задать свои варианты ответов (+ возможность своего ответа)\n"
        "3. Только свой ответ (пользователь может ввести любой текст)\n"
        "4. Завершить создание опроса",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "use_default_answers")
async def process_use_default_answers(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        data = await state.get_data()
        answers = data.get("answers", [])
        answers.append(["Да", "Нет", "Свой ответ"])
        await state.update_data(answers=answers)
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="➕ Добавить ещё вопрос", callback_data="continue_questions"),
                InlineKeyboardButton(text="✅ Завершить", callback_data="done_adding_questions")
            ]]
        )
        await callback.message.edit_text(
            "✅ Добавлены стандартные варианты ответов (Да/Нет + Свой ответ).\n\n"
            "Выберите действие:\n"
            "• Добавить ещё один вопрос\n"
            "• Завершить создание опроса",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при установке стандартных ответов: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    await callback.answer()

@dp.callback_query(F.data == "set_custom_answers")
async def process_set_custom_answers(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        await callback.message.edit_text(
            "Введите варианты ответов, разделяя их запятой (например: Отлично, Хорошо, Плохо)"
        )
        await state.set_state(CreateSurveyState.waiting_for_answers)
    except Exception as e:
        logger.error(f"Ошибка при запросе пользовательских ответов: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    await callback.answer()

@dp.message(CreateSurveyState.waiting_for_answers)
async def process_custom_answers(message: Message, state: FSMContext) -> None:
    try:
        custom_answers = [answer.strip() for answer in message.text.split(",") if answer.strip()]
        
        if not custom_answers:
            await message.answer("Необходимо ввести хотя бы один вариант ответа. Попробуйте снова:")
            return
            
        if len(custom_answers) > 10:  # Максимальное количество вариантов ответа
            await message.answer("Слишком много вариантов ответа. Максимум 10. Попробуйте снова:")
            return
            
        data = await state.get_data()
        answers = data.get("answers", [])
        custom_answers.append("Свой ответ")  # Всегда добавляем возможность своего ответа
        answers.append(custom_answers)
        await state.update_data(answers=answers)
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="➕ Добавить ещё вопрос", callback_data="continue_questions"),
                InlineKeyboardButton(text="✅ Завершить", callback_data="done_adding_questions")
            ]]
        )
        await message.answer(
            f"✅ Варианты ответов добавлены:\n" +
            "\n".join([f"• {answer}" for answer in custom_answers[:-1]]) +
            "\n• ✍️ Свой ответ\n\n"
            "Выберите действие:\n"
            "• Добавить ещё один вопрос\n"
            "• Завершить создание опроса",
            reply_markup=keyboard
        )
        await state.set_state(CreateSurveyState.waiting_for_questions)
    except Exception as e:
        logger.error(f"Ошибка при сохранении пользовательских ответов: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.callback_query(F.data == "custom_answer_only")
async def process_custom_answer_only(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        data = await state.get_data()
        answers = data.get("answers", [])
        answers.append(["Свой ответ"])  # Только опция своего ответа
        await state.update_data(answers=answers)
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="➕ Добавить ещё вопрос", callback_data="continue_questions"),
                InlineKeyboardButton(text="✅ Завершить", callback_data="done_adding_questions")
            ]]
        )
        await callback.message.edit_text(
            "✅ Добавлен вопрос с возможностью только своего ответа.\n\n"
            "Выберите действие:\n"
            "• Добавить ещё один вопрос\n"
            "• Завершить создание опроса",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при установке опции своего ответа: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    await callback.answer()

@dp.callback_query(F.data == "done_adding_questions")
async def process_done_adding_questions(callback: CallbackQuery, state: FSMContext) -> None:
    """Обработчик завершения добавления вопросов"""
    try:
        if not is_admin(callback.from_user.id):
            await callback.answer("У вас нет прав для создания опросов.", show_alert=True)
            return

        data = await state.get_data()
        questions = data.get("questions", [])

        if not questions:
            await callback.message.answer(
                "❌ Необходимо добавить хотя бы один вопрос.\n"
                "Введите текст вопроса:"
            )
            await callback.answer()
            return

        if len(questions) > Config.MAX_QUESTIONS:
            await callback.message.answer(
                f"❌ Превышено максимальное количество вопросов ({Config.MAX_QUESTIONS}).\n"
                "Удалите лишние вопросы через меню редактирования."
            )
            await callback.answer()
            return

        try:
            survey = Survey(
                name=data["name"],
                description=data["description"],
                questions=questions,
                answers=data.get("answers")
            )
        except ValueError as e:
            await callback.message.answer(f"❌ Ошибка при создании опроса: {str(e)}")
            await callback.answer()
            return

        surveys = await load_surveys()
        if len(surveys) >= Config.MAX_SURVEYS:
            await callback.message.answer(
                f"❌ Достигнут лимит количества опросов ({Config.MAX_SURVEYS}).\n"
                "Удалите неиспользуемые опросы перед созданием нового."
            )
            await callback.answer()
            return

        surveys[survey.survey_id] = survey
        await save_surveys(surveys)

        # Если нет активного опроса, делаем новый опрос активным
        active_survey_id = await get_active_survey_id()
        if not active_survey_id:
            await set_active_survey(survey.survey_id)
            activation_status = "\n✅ Опрос автоматически установлен как активный."
        else:
            activation_status = "\nℹ️ Чтобы сделать опрос активным, используйте меню управления опросами."

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
            ]]
        )
        
        await callback.message.edit_text(
            f"✅ Опрос \"{survey.name}\" успешно создан!\n"
            f"📋 Количество вопросов: {len(questions)}"
            f"{activation_status}",
            reply_markup=keyboard
        )
        
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при завершении создания опроса: {e}")
        await callback.message.answer(
            "❌ Произошла ошибка при создании опроса. Попробуйте позже."
        )
    
    await callback.answer()

@dp.callback_query(F.data == "start_survey")
async def process_start_survey(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        user_id = callback.from_user.id
        active_survey_id = await get_active_survey_id()
        
        if not active_survey_id:
            await callback.message.answer("В данный момент нет активного опроса.")
            await callback.answer()
            return

        surveys = await load_surveys()
        survey = surveys.get(active_survey_id)
        if not survey:
            await callback.message.answer("Произошла ошибка при загрузке опроса.")
            await callback.answer()
            return

        await state.update_data(
            current_question=0,
            survey_id=active_survey_id,
            answers=[]
        )

        # Создаем reply-клавиатуру с вариантами ответов для текущего вопроса
        answer_buttons = []
        if hasattr(survey, 'answers') and survey.answers and len(survey.answers) > 0:
            current_answers = survey.answers[0]
            for answer in current_answers:
                if answer == "Свой ответ":
                    answer_buttons.append([KeyboardButton(text="✍️ Свой ответ")])
                else:
                    answer_buttons.append([KeyboardButton(text=answer)])
        else:
            answer_buttons = [
                [KeyboardButton(text="Да ✅")],
                [KeyboardButton(text="Нет ❌")],
                [KeyboardButton(text="✍️ Свой ответ")]
            ]

        keyboard = ReplyKeyboardMarkup(
            keyboard=answer_buttons,
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await callback.message.delete()
        await callback.message.answer(
            f"Вопрос 1 из {len(survey.questions)}:\n\n{survey.questions[0]}",
            reply_markup=keyboard
        )
        await state.set_state(SurveyState.waiting_for_answer)
    except Exception as e:
        logger.error(f"Ошибка при начале опроса: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.message(SurveyState.waiting_for_answer)
async def process_survey_answer(message: Message, state: FSMContext) -> None:
    try:
        answer = message.text.strip()
        if not answer:
            await message.answer("Ответ не может быть пустым. Пожалуйста, выберите вариант ответа или введите свой:")
            return
            
        if len(answer) > Config.MAX_ANSWER_LENGTH:
            await message.answer(f"Ответ слишком длинный. Максимальная длина: {Config.MAX_ANSWER_LENGTH} символов")
            return

        data = await state.get_data()
        current_question = data.get("current_question", 0)
        survey_id = data.get("survey_id")
        answers = data.get("answers", [])

        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        if not survey:
            await message.answer(
                "Произошла ошибка при загрузке опроса.",
                reply_markup=ReplyKeyboardRemove()
            )
            await state.clear()
            return

        # Преобразуем стандартные ответы
        if answer == "Да ✅":
            answer = "Да"
        elif answer == "Нет ❌":
            answer = "Нет"
        elif answer == "✍️ Свой ответ":
            await message.answer(
                "Пожалуйста, введите ваш ответ:",
                reply_markup=ReplyKeyboardRemove()
            )
            return

        answers.append(answer)
        current_question += 1

        if current_question < len(survey.questions):
            await state.update_data(
                current_question=current_question,
                answers=answers
            )

            # Создаем reply-клавиатуру для следующего вопроса
            answer_buttons = []
            if hasattr(survey, 'answers') and survey.answers and len(survey.answers) > current_question:
                current_answers = survey.answers[current_question]
                for answer in current_answers:
                    if answer == "Свой ответ":
                        answer_buttons.append([KeyboardButton(text="✍️ Свой ответ")])
                    else:
                        answer_buttons.append([KeyboardButton(text=answer)])
            else:
                answer_buttons = [
                    [KeyboardButton(text="Да ✅")],
                    [KeyboardButton(text="Нет ❌")],
                    [KeyboardButton(text="✍️ Свой ответ")]
                ]

            keyboard = ReplyKeyboardMarkup(
                keyboard=answer_buttons,
                resize_keyboard=True,
                one_time_keyboard=True
            )
            
            await message.answer(
                f"Вопрос {current_question + 1} из {len(survey.questions)}:\n\n{survey.questions[current_question]}",
                reply_markup=keyboard
            )
        else:
            user_id = message.from_user.id
            username = message.from_user.username or "НетUsername"
            
            survey_results = [
                "━━━━━━━━━━━━━━━━━━━━━━",
                f"👤 Пользователь: @{username}",
                f"🆔 ID: {user_id}_{survey_id}",
                f"📝 Опрос: {survey.name}"
            ]
            for i, (q, a) in enumerate(zip(survey.questions, answers), 1):
                survey_results.extend([
                    f"❓ Вопрос {i}:",
                    f"└─ {q}",
                    f"✍️ Ответ:",
                    f"└─ {a}"
                ])
            survey_results.extend([
                "✅ Опрос завершён",
                "━━━━━━━━━━━━━━━━━━━━━━"
            ])
            await safe_append_file(Config.DATA_FILE, "\n".join(survey_results))
            await message.answer(
                "✨ Спасибо за участие в опросе!\n"
                "📋 Ваши ответы сохранены.",
                reply_markup=ReplyKeyboardRemove()
            )
            await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при обработке ответа: {e}")
        await message.answer(
            "Произошла ошибка. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()

@dp.callback_query(F.data == "manage_surveys")
async def process_manage_surveys(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для управления опросами.", show_alert=True)
        return

    try:
        surveys = await load_surveys()
        active_survey_id = await get_active_survey_id()

        if not surveys:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer(
                "Нет созданных опросов. Создайте новый опрос!",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons = []
        for survey_id, survey in surveys.items():
            status = "✅ " if survey_id == active_survey_id else ""
            buttons.append([
                InlineKeyboardButton(
                    text=f"{status}{survey.name}",
                    callback_data=f"survey_{survey_id}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.delete()
        await callback.message.answer(
            "Выберите опрос для управления:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при отображении списка опросов: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("survey_"))
async def process_survey_actions(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для управления опросами.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("survey_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        active_survey_id = await get_active_survey_id()
        is_active = survey_id == active_survey_id

        status_text = "✅ Активный" if is_active else "❌ Неактивный"
        toggle_text = "❌ Деактивировать" if is_active else "✅ Сделать активным"

        buttons = [
            [InlineKeyboardButton(
                text=toggle_text,
                callback_data=f"toggle_active_{survey_id}"
            )],
            [InlineKeyboardButton(
                text="👀 Просмотреть вопросы",
                callback_data=f"view_questions_{survey_id}"
            )],
            [InlineKeyboardButton(
                text="✏️ Редактировать вопрос",
                callback_data=f"edit_question_{survey_id}"
            )],
            [InlineKeyboardButton(
                text="➕ Добавить вопрос",
                callback_data=f"add_question_{survey_id}"
            )],
            [InlineKeyboardButton(
                text="❌ Удалить вопрос",
                callback_data=f"delete_question_{survey_id}"
            )],
            [InlineKeyboardButton(
                text="🗑 Удалить опрос",
                callback_data=f"delete_survey_{survey_id}"
            )],
            [InlineKeyboardButton(
                text="⬅️ Вернуться к списку опросов",
                callback_data="manage_surveys"
            )]
        ]

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(
            f"Управление опросом: {survey.name}\n"
            f"Описание: {survey.description}\n"
            f"Количество вопросов: {len(survey.questions)}\n"
            f"Статус: {status_text}",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при отображении действий с опросом: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("toggle_active_"))
async def process_toggle_active(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для управления опросами.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("toggle_active_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден или был удален.")
            await callback.answer()
            return
            
        active_survey_id = await get_active_survey_id()

        if survey_id == active_survey_id:
            # Деактивируем опрос
            await set_active_survey("")
            await callback.answer("Опрос деактивирован", show_alert=True)
        else:
            # Активируем опрос
            await set_active_survey(survey_id)
            await callback.answer(f"Опрос '{survey.name}' активирован", show_alert=True)

        # Обновляем информацию об опросе
        await process_survey_actions(callback)
    except Exception as e:
        logger.error(f"Ошибка при изменении активного опроса: {e}")
        await callback.message.answer(
            "Произошла ошибка при изменении статуса опроса. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
        await callback.answer()

@dp.callback_query(F.data.startswith("view_questions_"))
async def process_view_questions(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра опросов.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("view_questions_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        questions_text = "\n\n".join([f"{i+1}. {q}" for i, q in enumerate(survey.questions)])
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад к управлению опросом", callback_data=f"survey_{survey_id}")
            ]]
        )
        await callback.message.delete()
        await callback.message.answer(
            f"📝 Вопросы опроса \"{survey.name}\":\n\n{questions_text}",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при просмотре вопросов: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_survey_"))
async def process_delete_survey(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для удаления опросов.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("delete_survey_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        del surveys[survey_id]
        await save_surveys(surveys)

        active_survey_id = await get_active_survey_id()
        if survey_id == active_survey_id:
            await set_active_survey("")

        await callback.answer(f"Опрос '{survey.name}' удален.", show_alert=True)
        await process_manage_surveys(callback)
    except Exception as e:
        logger.error(f"Ошибка при удалении опроса: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
        await callback.answer()

@dp.callback_query(F.data == "broadcast")
async def process_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для рассылки сообщений.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
        ]]
    )
    await callback.message.delete()
    await callback.message.answer(
        "Введите текст сообщения для рассылки всем пользователям:",
        reply_markup=keyboard
    )
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.answer()

@dp.message(BroadcastState.waiting_for_message)
async def process_broadcast_message(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав для рассылки сообщений.")
        await state.clear()
        return

    try:
        broadcast_text = message.text.strip()
        if not broadcast_text:
            await message.answer("❌ Сообщение не может быть пустым. Введите текст сообщения:")
            return

        # Собираем уникальные ID пользователей из файла данных
        users = set()
        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            if content:
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith("🆔 ID:"):  # Ищем строки с ID
                        try:
                            user_id = int(line.split("🆔 ID: ")[1].split("_")[0])
                            users.add(user_id)
                        except (IndexError, ValueError) as e:
                            logger.warning(f"Не удалось распарсить ID из строки: {line}. Ошибка: {e}")
                            continue

        if not users:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
                ]])
            await message.answer(
                "ℹ️ Нет пользователей для рассылки. Возможно, никто еще не проходил опросы.",
                reply_markup=keyboard
            )
            await state.clear()
            return

        sent_count = 0
        failed_count = 0
        for user_id in users:
            try:
                await bot.send_message(user_id, broadcast_text)
                sent_count += 1
                await asyncio.sleep(0.05)  # Задержка для соблюдения лимитов Telegram
            except Exception as e:
                failed_count += 1
                logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
            ]])
        result_message = (
            f"✅ Рассылка завершена!\n"
            f"📤 Отправлено: {sent_count} из {len(users)} пользователей.\n"
            f"❌ Не удалось отправить: {failed_count}"
        )
        await message.answer(result_message, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Критическая ошибка при рассылке: {e}")
        await message.answer("❌ Произошла ошибка при рассылке. Попробуйте позже.")

    await state.clear()

@dp.callback_query(F.data == "view_stats")
async def process_view_stats(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра статистики.", show_alert=True)
        return

    try:
        surveys = await load_surveys()
        if not surveys:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer(
                "Нет созданных опросов для просмотра статистики.",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons = []
        for survey_id, survey in surveys.items():
            buttons.append([
                InlineKeyboardButton(
                    text=f"📊 {survey.name}",
                    callback_data=f"stats_{survey_id}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.delete()
        await callback.message.answer(
            "Выберите опрос для просмотра статистики:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при отображении списка опросов для статистики: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("stats_"))
async def process_survey_stats(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав для просмотра статистики.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("stats_", "")
        logger.info(f"Запрошена статистика для опроса с ID: {survey_id}")
        
        surveys = await load_surveys()
        logger.info(f"Загруженные опросы: {surveys}")
        
        if not surveys or survey_id not in surveys:
            logger.warning(f"Опрос с ID {survey_id} не найден")
            await callback.message.edit_text(
                "Опрос не найден.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="view_stats")
                ]])
            )
            await callback.answer()
            return
            
        survey = surveys[survey_id]
        logger.info(f"Тип объекта survey: {type(survey)}, значение: {survey}")
        
        # Пытаемся восстановить объект Survey
        if isinstance(survey, str):
            try:
                # Пробуем распарсить JSON если это строка
                survey_data = json.loads(survey)
                survey = Survey(
                    name=survey_data["name"],
                    description=survey_data["description"],
                    questions=survey_data["questions"],
                    answers=survey_data.get("answers"),
                    survey_id=survey_data.get("survey_id")
                )
            except json.JSONDecodeError:
                logger.error(f"Не удалось распарсить строку как JSON: {survey}")
                await callback.message.edit_text(
                    "Ошибка: повреждены данные опроса.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="⬅️ Назад", callback_data="view_stats")
                    ]])
                )
                await callback.answer()
                return
            except Exception as e:
                logger.error(f"Ошибка при создании объекта Survey из строки: {e}")
                await callback.message.edit_text(
                    "Ошибка при обработке данных опроса.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="⬅️ Назад", callback_data="view_stats")
                    ]])
                )
                await callback.answer()
                return
        elif not isinstance(survey, Survey):
            try:
                if isinstance(survey, dict):
                    logger.info(f"Преобразование dict в Survey объект. Данные: {survey}")
                    survey = Survey(
                        name=survey["name"],
                        description=survey["description"],
                        questions=survey["questions"],
                        answers=survey.get("answers"),
                        survey_id=survey.get("survey_id")
                    )
                else:
                    logger.error(f"Неподдерживаемый тип данных опроса: {type(survey)}")
                    await callback.message.edit_text(
                        "Ошибка: некорректные данные опроса.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="⬅️ Назад", callback_data="view_stats")
                        ]])
                    )
                    await callback.answer()
                    return
            except Exception as e:
                logger.error(f"Ошибка при создании объекта опроса из dict: {e}")
                await callback.message.edit_text(
                    "Ошибка при обработке данных опроса.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="⬅️ Назад", callback_data="view_stats")
                    ]])
                )
                await callback.answer()
                return

        logger.info(f"Успешно получен объект опроса: {survey.name}")

        # Множества для хранения уникальных пользователей
        unique_users = set()
        completed_users = set()

        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            sections = content.split("━━━━━━━━━━━━━━━━━━━━━━")
            
            for section in sections:
                if not section.strip():
                    continue
                
                lines = [line.strip() for line in section.split('\n') if line.strip()]
                
                current_section_user_id = None
                current_section_survey_id = None
                is_completed = False
                
                for line in lines:
                    if "🆔 ID:" in line:
                        try:
                            id_info = line.split("ID: ")[1]
                            user_id, survey_info = id_info.split("_")
                            current_section_user_id = user_id.strip()
                            current_section_survey_id = survey_info.strip()
                        except Exception as e:
                            logger.error(f"Ошибка при парсинге ID: {e}")
                            continue
                    elif "✅ Опрос завершён" in line:
                        is_completed = True
                
                # Проверяем, относится ли секция к нужному опросу
                if current_section_survey_id == survey_id and current_section_user_id:
                    unique_users.add(current_section_user_id)
                    if is_completed:
                        completed_users.add(current_section_user_id)

        total_users = len(unique_users)
        completed_count = len(completed_users)
        completion_percentage = (completed_count/total_users*100) if total_users > 0 else 0
        
        logger.info(f"Статистика собрана: всего пользователей - {total_users}, завершили - {completed_count}")
        
        stats_text = [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📊 Статистика опроса \"{survey.name}\"",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "👥 Общая информация:",
            f"├─ Всего уникальных участников: {total_users}",
            f"├─ Завершили опрос: {completed_count}",
            f"└─ Процент завершения: {completion_percentage:.1f}%",
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад к списку опросов", callback_data="view_stats")
            ]]
        )

        try:
            await callback.message.edit_text("\n".join(stats_text), reply_markup=keyboard)
        except Exception as edit_error:
            logger.error(f"Ошибка при редактировании сообщения: {edit_error}")
            # Если не удалось отредактировать, отправляем новое сообщение
            await callback.message.answer("\n".join(stats_text), reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                "Произошла ошибка при получении статистики. Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="view_stats")
                ]])
            )
        except Exception as edit_error:
            logger.error(f"Ошибка при отправке сообщения об ошибке: {edit_error}")
            await callback.message.answer(
                "Произошла ошибка при получении статистики. Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="view_stats")
                ]])
            )
    
    await callback.answer()

@dp.callback_query(F.data == "view_user_answers")
async def process_view_user_answers(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра ответов.", show_alert=True)
        return

    try:
        surveys = await load_surveys()
        logger.info(f"Загружены опросы для просмотра ответов: {surveys}")
        
        if not surveys:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer(
                "Нет созданных опросов для просмотра ответов.",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons = []
        for survey_id, survey in surveys.items():
            try:
                # Проверяем тип объекта survey и преобразуем при необходимости
                if isinstance(survey, str):
                    try:
                        survey_data = json.loads(survey)
                        survey = Survey(
                            name=survey_data["name"],
                            description=survey_data["description"],
                            questions=survey_data["questions"],
                            answers=survey_data.get("answers"),
                            survey_id=survey_data.get("survey_id")
                        )
                    except json.JSONDecodeError:
                        logger.error(f"Не удалось распарсить строку как JSON для опроса {survey_id}: {survey}")
                        continue
                    except Exception as e:
                        logger.error(f"Ошибка при создании объекта Survey из строки для опроса {survey_id}: {e}")
                        continue
                elif not isinstance(survey, Survey):
                    if isinstance(survey, dict):
                        try:
                            survey = Survey(
                                name=survey["name"],
                                description=survey["description"],
                                questions=survey["questions"],
                                answers=survey.get("answers"),
                                survey_id=survey.get("survey_id")
                            )
                        except Exception as e:
                            logger.error(f"Ошибка при создании объекта Survey из dict для опроса {survey_id}: {e}")
                            continue
                    else:
                        logger.error(f"Неподдерживаемый тип данных опроса {survey_id}: {type(survey)}")
                        continue

                buttons.append([
                    InlineKeyboardButton(
                        text=f"📋 {survey.name}",
                        callback_data=f"view_answers_survey_{survey_id}"
                    )
                ])
            except Exception as e:
                logger.error(f"Ошибка при обработке опроса {survey_id}: {e}")
                continue

        if not buttons:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer(
                "Ошибка при загрузке опросов. Попробуйте позже.",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons.append([
            InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.delete()
        await callback.message.answer(
            "Выберите опрос для просмотра ответов пользователей:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при отображении списка опросов: {e}", exc_info=True)
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("view_answers_survey_"))
async def process_view_survey_users(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра ответов.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("view_answers_survey_", "")
        logger.info(f"Запрошен просмотр ответов для опроса с ID: {survey_id}")
        
        surveys = await load_surveys()
        if not surveys or survey_id not in surveys:
            logger.warning(f"Опрос с ID {survey_id} не найден")
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return
            
        survey = surveys[survey_id]
        logger.info(f"Тип объекта survey: {type(survey)}, значение: {survey}")
        
        # Пытаемся восстановить объект Survey
        if isinstance(survey, str):
            try:
                # Пробуем распарсить JSON если это строка
                survey_data = json.loads(survey)
                survey = Survey(
                    name=survey_data["name"],
                    description=survey_data["description"],
                    questions=survey_data["questions"],
                    answers=survey_data.get("answers"),
                    survey_id=survey_data.get("survey_id")
                )
            except json.JSONDecodeError:
                logger.error(f"Не удалось распарсить строку как JSON: {survey}")
                await callback.message.answer("Ошибка: повреждены данные опроса.")
                await callback.answer()
                return
            except Exception as e:
                logger.error(f"Ошибка при создании объекта Survey из строки: {e}")
                await callback.message.answer("Ошибка при обработке данных опроса.")
                await callback.answer()
                return
        elif not isinstance(survey, Survey):
            try:
                if isinstance(survey, dict):
                    logger.info(f"Преобразование dict в Survey объект. Данные: {survey}")
                    survey = Survey(
                        name=survey["name"],
                        description=survey["description"],
                        questions=survey["questions"],
                        answers=survey.get("answers"),
                        survey_id=survey.get("survey_id")
                    )
                else:
                    logger.error(f"Неподдерживаемый тип данных опроса: {type(survey)}")
                    await callback.message.answer("Ошибка: некорректные данные опроса.")
                    await callback.answer()
                    return
            except Exception as e:
                logger.error(f"Ошибка при создании объекта опроса из dict: {e}")
                await callback.message.answer("Ошибка при обработке данных опроса.")
                await callback.answer()
                return

        logger.info(f"Успешно получен объект опроса: {survey.name}")

        users = {}
        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            current_user_id = None
            current_username = None

            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue

                if "👤 Пользователь: @" in line:
                    current_username = line.split("@")[1]
                elif "🆔 ID:" in line:
                    try:
                        parts = line.split("ID: ")[1].split("_")
                        current_user_id = parts[0]
                        current_survey = parts[1]
                        if current_survey == survey_id:
                            users[current_user_id] = current_username
                    except:
                        continue

        if not users:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад к списку опросов", callback_data="view_user_answers")
                ]]
            )
            await callback.message.edit_text(
                f"Нет пользователей, прошедших опрос \"{survey.name}\".",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons = []
        for user_id, username in users.items():
            display_name = f"@{username}" if username != "НетUsername" else f"ID: {user_id}"
            buttons.append([
                InlineKeyboardButton(
                    text=f"👤 {display_name}",
                    callback_data=f"user_answers_{user_id}_{survey_id}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад к списку опросов", callback_data="view_user_answers")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(
            f"Выберите пользователя для просмотра ответов\n"
            f"Опрос: {survey.name}",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка при отображении списка пользователей: {e}", exc_info=True)
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("user_answers_"))
async def process_user_answers(callback: CallbackQuery) -> None:
    """Обработка запроса на просмотр ответов пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра ответов.", show_alert=True)
        return

    try:
        # Получаем user_id и survey_id из callback data
        _, _, user_id, survey_id = callback.data.split("_")
        logger.info(f"Запрошены ответы пользователя {user_id} для опроса {survey_id}")

        # Проверяем наличие файла с ответами
        if not os.path.exists(Config.DATA_FILE):
            logger.warning(f"Файл с ответами не найден: {Config.DATA_FILE}")
            await callback.message.edit_text(
                "ℹ️ Пока нет сохраненных ответов на опросы.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="view_user_answers")
                ]])
            )
            return

        # Читаем содержимое файла
        content = await safe_read_file(Config.DATA_FILE)
        if not content:
            await callback.message.edit_text(
                "ℹ️ Файл с ответами пуст.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="view_user_answers")
                ]])
            )
            return

        # Ищем ответы конкретного пользователя
        sections = content.split("━━━━━━━━━━━━━━━━━━━━━━")
        user_answers = []
        username = None
        survey_name = None

        for section in sections:
            if not section.strip():
                continue

            lines = [line.strip() for line in section.split('\n') if line.strip()]
            if not lines:
                continue

            # Проверяем, относится ли секция к нужному пользователю и опросу
            current_user_id = None
            current_survey_id = None
            current_username = None
            current_survey_name = None
            current_answers = []

            for i, line in enumerate(lines):
                if "👤 Пользователь: @" in line:
                    current_username = line.split("@")[1].strip()
                elif "🆔 ID:" in line:
                    id_parts = line.split("ID: ")[1].split("_")
                    if len(id_parts) >= 2:
                        current_user_id = id_parts[0].strip()
                        current_survey_id = id_parts[1].strip()
                elif "📝 Опрос:" in line:
                    current_survey_name = line.split("Опрос:")[1].strip()
                elif "❓ Вопрос" in line and i + 3 < len(lines):
                    question = lines[i + 1].replace("└─", "").strip()
                    answer = lines[i + 3].replace("└─", "").strip()
                    current_answers.append((question, answer))

            # Если нашли нужную секцию, сохраняем данные
            if current_user_id == user_id and current_survey_id == survey_id:
                user_answers = current_answers
                username = current_username
                survey_name = current_survey_name
                break

        if not user_answers:
            await callback.message.edit_text(
                f"ℹ️ Ответы пользователя для этого опроса не найдены.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_answers_survey_{survey_id}")
                ]])
            )
            return

        # Формируем сообщение с ответами
        message_parts = []
        current_part = [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 Ответы пользователя @{username}",
            f"📋 Опрос: {survey_name}",
            "━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, (question, answer) in enumerate(user_answers, 1):
            answer_block = [
                f"❓ Вопрос {idx}:",
                f"└─ {question}",
                "✍️ Ответ:",
                f"└─ {answer}",
                ""
            ]
            
            # Проверяем, не превысит ли добавление нового блока лимит
            if len("\n".join(current_part + answer_block)) > 3800:
                message_parts.append("\n".join(current_part))
                current_part = answer_block
            else:
                current_part.extend(answer_block)

        if current_part:
            current_part.append("━━━━━━━━━━━━━━━━━━━━━━")
            message_parts.append("\n".join(current_part))

        # Отправляем сообщения
        for idx, message_text in enumerate(message_parts):
            if idx == 0:
                await callback.message.edit_text(
                    message_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_answers_survey_{survey_id}")
                    ]]) if idx == len(message_parts) - 1 else None
                )
            else:
                await callback.message.answer(
                    message_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_answers_survey_{survey_id}")
                    ]]) if idx == len(message_parts) - 1 else None
                )

    except Exception as e:
        logger.error(f"Ошибка при обработке ответов: {str(e)}")
        await callback.message.edit_text(
            "❌ Произошла ошибка при получении ответов. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_answers_survey_{survey_id}")
            ]])
        )

    await callback.answer()

@dp.callback_query(F.data.startswith("edit_question_"))
async def process_edit_question_select(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для редактирования вопросов.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("edit_question_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        await state.update_data(survey_id=survey_id)
        
        buttons = []
        for i, question in enumerate(survey.questions, 1):
            buttons.append([
                InlineKeyboardButton(
                    text=f"Вопрос {i}",
                    callback_data=f"select_question_{i}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"survey_{survey_id}")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.delete()
        await callback.message.answer(
            "Выберите вопрос для редактирования:\n\n" +
            "\n".join(f"{i}. {q}" for i, q in enumerate(survey.questions, 1)),
            reply_markup=keyboard
        )
        await state.set_state(EditSurveyState.waiting_for_question_number)
    except Exception as e:
        logger.error(f"Ошибка при выборе вопроса для редактирования: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("select_question_"), EditSurveyState.waiting_for_question_number)
async def process_edit_question(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        question_num = int(callback.data.replace("select_question_", ""))
        data = await state.get_data()
        survey_id = data.get("survey_id")
        
        await state.update_data(question_number=question_num)
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"survey_{survey_id}")
            ]]
        )
        await callback.message.delete()
        await callback.message.answer(
            "Введите новый текст вопроса:",
            reply_markup=keyboard
        )
        await state.set_state(EditSurveyState.waiting_for_edited_question)
    except Exception as e:
        logger.error(f"Ошибка при запросе нового текста вопроса: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.message(EditSurveyState.waiting_for_edited_question)
async def process_save_edited_question(message: Message, state: FSMContext) -> None:
    try:
        new_question = message.text.strip()
        if not new_question:
            await message.answer("Текст вопроса не может быть пустым. Введите текст вопроса:")
            return

        data = await state.get_data()
        survey_id = data.get("survey_id")
        question_num = data.get("question_number")

        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        if not survey:
            await message.answer("Опрос не найден.")
            await state.clear()
            return

        survey.questions[question_num - 1] = new_question
        await save_surveys(surveys)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад к управлению опросом", callback_data=f"survey_{survey_id}")
            ]]
        )
        await message.answer(
            "✅ Вопрос успешно отредактирован!",
            reply_markup=keyboard
        )
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при сохранении отредактированного вопроса: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()

@dp.callback_query(F.data.startswith("delete_question_"))
async def process_delete_question_select(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для удаления вопросов.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("delete_question_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        if len(survey.questions) <= 1:
            await callback.message.answer(
                "Нельзя удалить последний вопрос опроса. Опрос должен содержать хотя бы один вопрос."
            )
            await callback.answer()
            return

        await state.update_data(survey_id=survey_id)
        
        buttons = []
        for i, question in enumerate(survey.questions, 1):
            buttons.append([
                InlineKeyboardButton(
                    text=f"Удалить вопрос {i}",
                    callback_data=f"confirm_delete_question_{i}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"survey_{survey_id}")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.delete()
        await callback.message.answer(
            "Выберите вопрос для удаления:\n\n" +
            "\n".join(f"{i}. {q}" for i, q in enumerate(survey.questions, 1)),
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при выборе вопроса для удаления: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_question_"))
async def process_delete_question(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        question_num = int(callback.data.replace("confirm_delete_question_", ""))
        data = await state.get_data()
        survey_id = data.get("survey_id")
        
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        survey.questions.pop(question_num - 1)
        await save_surveys(surveys)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад к управлению опросом", callback_data=f"survey_{survey_id}")
            ]]
        )
        await callback.message.delete()
        await callback.message.answer(
            "✅ Вопрос успешно удален!",
            reply_markup=keyboard
        )
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при удалении вопроса: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("add_question_"))
async def process_add_question(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для добавления вопросов.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("add_question_", "")
        await state.update_data(survey_id=survey_id)
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"survey_{survey_id}")
            ]]
        )
        await callback.message.delete()
        await callback.message.answer(
            "Введите текст нового вопроса:",
            reply_markup=keyboard
        )
        await state.set_state(EditSurveyState.waiting_for_new_question)
    except Exception as e:
        logger.error(f"Ошибка при запросе нового вопроса: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.message(EditSurveyState.waiting_for_new_question)
async def process_save_new_question(message: Message, state: FSMContext) -> None:
    try:
        new_question = message.text.strip()
        if not new_question:
            await message.answer("Текст вопроса не может быть пустым. Введите текст вопроса:")
            return

        data = await state.get_data()
        survey_id = data.get("survey_id")

        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        if not survey:
            await message.answer("Опрос не найден.")
            await state.clear()
            return

        survey.questions.append(new_question)
        await save_surveys(surveys)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад к управлению опросом", callback_data=f"survey_{survey_id}")
            ]]
        )
        await message.answer(
            "✅ Новый вопрос успешно добавлен!",
            reply_markup=keyboard
        )
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при сохранении нового вопроса: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()

@dp.callback_query(F.data == "download_data")
async def process_download_data(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для скачивания данных.", show_alert=True)
        return

    try:
        surveys = await load_surveys()
        if not surveys:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer(
                "Нет созданных опросов для скачивания данных.",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons = []
        for survey_id, survey in surveys.items():
            buttons.append([
                InlineKeyboardButton(
                    text=f"📥 {survey.name}",
                    callback_data=f"download_survey_{survey_id}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(text="📥 Скачать все данные", callback_data="download_all_data")
        ])
        buttons.append([
            InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.delete()
        await callback.message.answer(
            "Выберите опрос для скачивания данных:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при отображении списка опросов для скачивания: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("download_survey_"))
async def process_download_survey_data(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для скачивания данных.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("download_survey_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        temp_file_path = os.path.join(bot_dir, f"survey_data_{survey_id}.txt")
        
        # Формируем заголовок с информацией об опросе
        survey_data = [
            "╔══════════════════════════════════════════",
            f"║ 📊 ДАННЫЕ ОПРОСА",
            f"║ Название: {survey.name}",
            f"║ Описание: {survey.description}",
            f"║ ID опроса: {survey_id}",
            "╠══════════════════════════════════════════",
            "║ 📝 ВОПРОСЫ:",
        ]
        
        # Добавляем список вопросов
        for i, question in enumerate(survey.questions, 1):
            survey_data.extend([
                f"║ {i}. {question}",
                "║    Варианты ответов:" if hasattr(survey, 'answers') and survey.answers else "║    Стандартные варианты ответов (Да/Нет)"
            ])
            if hasattr(survey, 'answers') and survey.answers and len(survey.answers) > i-1:
                for answer in survey.answers[i-1]:
                    survey_data.append(f"║    • {answer}")
            survey_data.append("║")

        survey_data.extend([
            "╠══════════════════════════════════════════",
            "║ 👥 ОТВЕТЫ ПОЛЬЗОВАТЕЛЕЙ:",
            "╠══════════════════════════════════════════"
        ])

        # Собираем ответы пользователей
        user_responses = {}
        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            current_user = None
            current_responses = []
            
            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue
                
                if "👤 Пользователь: @" in line:
                    if current_user and current_responses:
                        user_responses[current_user] = current_responses
                    current_user = line
                    current_responses = []
                elif "🆔 ID:" in line:
                    try:
                        current_survey = line.split("_")[1]
                        if current_survey != survey_id:
                            current_user = None
                            current_responses = []
                    except:
                        continue
                elif current_user and "└─" in line:
                    current_responses.append(line.replace("└─ ", ""))
            
            if current_user and current_responses:
                user_responses[current_user] = current_responses

        # Добавляем ответы пользователей в отформатированном виде
        if user_responses:
            for user, responses in user_responses.items():
                survey_data.extend([
                    "║",
                    f"║ {user}",
                    "║ Ответы:"
                ])
                
                for i, (question, answer) in enumerate(zip(survey.questions, responses[::2]), 1):
                    response_idx = (i-1) * 2 + 1
                    if response_idx < len(responses):
                        survey_data.extend([
                            f"║ {i}. {question}",
                            f"║    Ответ: {responses[response_idx]}"
                        ])
                survey_data.append("║ ─────────────────────────────────")
        else:
            survey_data.extend([
                "║",
                "║ Пока нет ответов на этот опрос",
                "║"
            ])

        survey_data.extend([
            "╚══════════════════════════════════════════",
            f"Дата выгрузки: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        ])

        async with aiofiles.open(temp_file_path, 'w', encoding=Config.FILE_ENCODING) as f:
            await f.write('\n'.join(survey_data))

        await callback.message.delete()
        await callback.message.answer_document(
            FSInputFile(temp_file_path, filename=f"survey_data_{survey.name}.txt"),
            caption=f"📊 Данные опроса \"{survey.name}\""
        )

        try:
            os.remove(temp_file_path)
        except:
            pass

    except Exception as e:
        logger.error(f"Ошибка при скачивании данных опроса: {e}")
        await callback.message.answer("Произошла ошибка при скачивании данных. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data == "download_all_data")
async def process_download_all_data(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для скачивания данных.", show_alert=True)
        return

    try:
        surveys = await load_surveys()
        if not surveys:
            await callback.message.answer("Нет данных для скачивания.")
            await callback.answer()
            return

        temp_file_path = os.path.join(bot_dir, "all_surveys_data.txt")
        
        all_data = [
            "╔══════════════════════════════════════════",
            "║ 📊 ДАННЫЕ ВСЕХ ОПРОСОВ",
            "╚══════════════════════════════════════════"
        ]
        
        for survey_id, survey in surveys.items():
            all_data.extend([
                "",
                "╔══════════════════════════════════════════",
                f"║ ОПРОС: {survey.name}",
                f"║ Описание: {survey.description}",
                f"║ ID опроса: {survey_id}",
                "╠══════════════════════════════════════════",
                "║ 📝 ВОПРОСЫ:"
            ])
            
            for i, question in enumerate(survey.questions, 1):
                all_data.extend([
                    f"║ {i}. {question}",
                    "║    Варианты ответов:" if hasattr(survey, 'answers') and survey.answers else "║    Стандартные варианты ответов (Да/Нет)"
                ])
                if hasattr(survey, 'answers') and survey.answers and len(survey.answers) > i-1:
                    for answer in survey.answers[i-1]:
                        all_data.append(f"║    • {answer}")
                all_data.append("║")

            all_data.extend([
                "╠══════════════════════════════════════════",
                "║ 👥 ОТВЕТЫ ПОЛЬЗОВАТЕЛЕЙ:",
                "╠══════════════════════════════════════════"
            ])

            # Собираем ответы пользователей для текущего опроса
            user_responses = {}
            if os.path.exists(Config.DATA_FILE):
                content = await safe_read_file(Config.DATA_FILE)
                current_user = None
                current_responses = []
                
                for line in content.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    
                    if "👤 Пользователь: @" in line:
                        if current_user and current_responses:
                            user_responses[current_user] = current_responses
                        current_user = line
                        current_responses = []
                    elif "🆔 ID:" in line:
                        try:
                            current_survey = line.split("_")[1]
                            if current_survey != survey_id:
                                current_user = None
                                current_responses = []
                        except:
                            continue
                    elif current_user and "└─" in line:
                        current_responses.append(line.replace("└─ ", ""))
                    
                    if current_user and current_responses:
                        user_responses[current_user] = current_responses

                if user_responses:
                    for user, responses in user_responses.items():
                        all_data.extend([
                            "║",
                            f"║ {user}",
                            "║ Ответы:"
                        ])
                        
                        for i, (question, answer) in enumerate(zip(survey.questions, responses[::2]), 1):
                            response_idx = (i-1) * 2 + 1
                            if response_idx < len(responses):
                                all_data.extend([
                                    f"║ {i}. {question}",
                                    f"║    Ответ: {responses[response_idx]}"
                                ])
                        all_data.append("║ ─────────────────────────────────")
                else:
                    all_data.extend([
                        "║",
                        "║ Пока нет ответов на этот опрос",
                        "║"
                    ])

                all_data.append("╚══════════════════════════════════════════")

            all_data.extend([
                "",
                f"Дата выгрузки: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            ])

        async with aiofiles.open(temp_file_path, 'w', encoding=Config.FILE_ENCODING) as f:
            await f.write('\n'.join(all_data))

        await callback.message.delete()
        await callback.message.answer_document(
            FSInputFile(temp_file_path, filename="all_surveys_data.txt"),
            caption="📊 Данные всех опросов"
        )

        try:
            os.remove(temp_file_path)
        except:
            pass

    except Exception as e:
        logger.error(f"Ошибка при скачивании всех данных: {e}")
        await callback.message.answer("Произошла ошибка при скачивании данных. Попробуйте позже.")
    
    await callback.answer()

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """
    Обработчик команды отмены текущей операции
    """
    try:
        current_state = await state.get_state()
        if current_state is None:
            await message.answer("🤔 Нет активной операции для отмены.")
            return
            
        await state.clear()
        await message.answer("✅ Операция отменена.")
        
        # Возврат в главное меню в зависимости от прав пользователя
        if is_admin(message.from_user.id):
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="Админ-панель 👨‍💻", callback_data="admin")
                ]]
            )
            await message.answer("Вернуться в админ-панель:", reply_markup=keyboard)
        else:
            await cmd_start(message)
    except Exception as e:
        logger.error(f"Ошибка при отмене операции: {e}")
        await message.answer("Произошла ошибка при отмене операции. Попробуйте позже.")

@dp.callback_query(F.data == "continue_questions")
async def process_continue_questions(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        await callback.message.edit_text(
            "Введите следующий вопрос:"
        )
        await state.set_state(CreateSurveyState.waiting_for_questions)
    except Exception as e:
        logger.error(f"Ошибка при продолжении создания вопросов: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    await callback.answer()

async def validate_surveys_file() -> None:
    """Проверка целостности файла с опросами"""
    try:
        if not os.path.exists(SURVEYS_FILE):
            logger.warning("Файл с опросами не найден")
            return

        content = await safe_read_file(SURVEYS_FILE)
        if not content or not content.strip():
            logger.warning("Файл с опросами пуст")
            return

        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                logger.error(f"Некорректный формат данных в файле опросов: {type(data)}")
                return

            # Проверяем каждый опрос
            for survey_id, survey_data in data.items():
                required_fields = ["name", "description", "questions"]
                missing_fields = [field for field in required_fields if field not in survey_data]
                if missing_fields:
                    logger.error(f"Опрос {survey_id}: отсутствуют обязательные поля: {missing_fields}")
                    continue

                # Проверяем answers
                answers = survey_data.get("answers", [])
                if not isinstance(answers, list):
                    logger.error(f"Опрос {survey_id}: некорректный формат answers")
                    continue

                # Проверяем соответствие количества ответов количеству вопросов
                questions = survey_data.get("questions", [])
                if len(answers) != len(questions):
                    logger.error(f"Опрос {survey_id}: количество ответов не соответствует количеству вопросов")
                    continue

            logger.info("Проверка файла с опросами успешно завершена")
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка при разборе JSON в файле опросов: {e}")
            return

    except Exception as e:
        logger.error(f"Ошибка при проверке файла с опросами: {e}")
        return

async def main() -> None:
    try:
        logger.info("Запуск бота...")
        
        # Проверка конфигурации
        Config.validate_config()
        
        # Проверка файла с опросами
        await validate_surveys_file()
        
        # Очистка временных файлов при запуске
        await cleanup_temp_files()
        
        # Запуск периодической очистки
        asyncio.create_task(cleanup_task())
        
        # Запуск бота
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise 