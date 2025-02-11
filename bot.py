import os
import time
import logging
import asyncio
from typing import List, Dict, Optional, Any
import aiofiles
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from config import Config
from utils import (
    safe_read_file, safe_write_file, safe_append_file,
    cleanup_temp_files, check_disk_space, check_file_size
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding=Config.FILE_ENCODING),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=Config.API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
bot_dir = os.path.join(desktop_path, "БОТ")
os.makedirs(bot_dir, exist_ok=True)

SURVEYS_DIR = os.path.join(bot_dir, "surveys")
os.makedirs(SURVEYS_DIR, exist_ok=True)
SURVEYS_FILE = os.path.join(SURVEYS_DIR, "surveys.txt")
ACTIVE_SURVEY_FILE = os.path.join(SURVEYS_DIR, "active_survey.txt")
DATA_FILE = os.path.join(bot_dir, "user_data.txt")

class SurveyState(StatesGroup):
    waiting_for_answer = State()

class CreateSurveyState(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_questions = State()

class EditSurveyState(StatesGroup):
    waiting_for_edited_question = State()
    waiting_for_question_number = State()
    waiting_for_new_question = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

class Survey:
    def __init__(self, name: str, description: str, questions: List[str], survey_id: Optional[str] = None):
        self.name = name
        self.description = description
        self.questions = questions
        self.survey_id = survey_id or str(int(time.time()))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "questions": self.questions,
            "survey_id": self.survey_id
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Survey':
        return cls(
            name=data["name"],
            description=data["description"],
            questions=data["questions"],
            survey_id=data["survey_id"]
        )

async def load_surveys() -> Dict[str, Survey]:
    try:
        content = await safe_read_file(Config.SURVEYS_FILE)
        if not content:
            return {}

        surveys = {}
        current_survey = None
        current_questions = []

        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith("SURVEY_ID:"):
                if current_survey:
                    surveys[current_survey["id"]] = Survey(
                        name=current_survey["name"],
                        description=current_survey["description"],
                        questions=current_questions,
                        survey_id=current_survey["id"]
                    )
                current_survey = {"id": line.replace("SURVEY_ID:", "").strip()}
                current_questions = []
            elif line.startswith("NAME:"):
                if current_survey:
                    current_survey["name"] = line.replace("NAME:", "").strip()
            elif line.startswith("DESCRIPTION:"):
                if current_survey:
                    current_survey["description"] = line.replace("DESCRIPTION:", "").strip()
            elif line.startswith("- "):
                if current_survey:
                    current_questions.append(line[2:])

        if current_survey:
            surveys[current_survey["id"]] = Survey(
                name=current_survey["name"],
                description=current_survey["description"],
                questions=current_questions,
                survey_id=current_survey["id"]
            )

        return surveys
    except Exception as e:
        logger.error(f"Ошибка при загрузке опросов: {e}")
        return {}

async def save_surveys(surveys: Dict[str, Survey]) -> None:
    try:
        lines = []
        for survey in surveys.values():
            lines.extend([
                f"SURVEY_ID: {survey.survey_id}",
                f"NAME: {survey.name}",
                f"DESCRIPTION: {survey.description}"
            ])
            for question in survey.questions:
                lines.append(f"- {question}")
            lines.append("")
        
        await safe_write_file(Config.SURVEYS_FILE, "\n".join(lines))
    except Exception as e:
        logger.error(f"Ошибка при сохранении опросов: {e}")

async def get_active_survey_id() -> Optional[str]:
    try:
        content = await safe_read_file(Config.ACTIVE_SURVEY_FILE)
        return content.strip() if content else None
    except Exception as e:
        logger.error(f"Ошибка при получении активного опроса: {e}")
        return None

async def set_active_survey(survey_id: str) -> None:
    try:
        await safe_write_file(Config.ACTIVE_SURVEY_FILE, survey_id)
    except Exception as e:
        logger.error(f"Ошибка при установке активного опроса: {e}")

def is_admin(user_id: int) -> bool:
    return user_id in Config.ADMIN_IDS

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    try:
        user_id = message.from_user.id
        logger.info(f"Пользователь {user_id} запустил бота")

        active_survey_id = await get_active_survey_id()
        if not active_survey_id:
            if is_admin(user_id):
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="Админ-панель 👨‍💻", callback_data="admin")
                    ]]
                )
                await message.answer(
                    "👋 Привет! В данный момент нет активного опроса.\n"
                    "Вы можете создать новый опрос через админ-панель.",
                    reply_markup=keyboard
                )
            else:
                await message.answer(
                    "👋 Привет! В данный момент нет активного опроса.\n"
                    "Пожалуйста, попробуйте позже."
                )
            return

        surveys = await load_surveys()
        survey = surveys.get(active_survey_id)
        if not survey:
            await message.answer("Произошла ошибка при загрузке опроса. Пожалуйста, попробуйте позже.")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="Начать опрос 📝", callback_data="start_survey")
            ]]
        )
        await message.answer(
            f"Привет! 👋\n\n"
            f"Доступен опрос: {survey.name}\n"
            f"Описание: {survey.description}\n\n"
            f"Нажмите кнопку ниже, чтобы начать:",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")

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
    
    if len(questions) >= Config.MAX_QUESTIONS:
        await message.answer(f"Достигнуто максимальное количество вопросов ({Config.MAX_QUESTIONS})")
        return
        
    questions.append(question)
    await state.update_data(questions=questions)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово", callback_data="done_adding_questions")
        ]]
    )
    await message.answer(
        f"✅ Вопрос #{len(questions)} добавлен.\n"
        "Введите следующий вопрос или нажмите 'Готово':",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "done_adding_questions")
async def process_done_adding_questions(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    questions = data.get("questions", [])

    if not questions:
        await callback.message.answer(
            "Необходимо добавить хотя бы один вопрос. Введите вопрос:"
        )
        await callback.answer()
        return

    survey = Survey(
        name=data["name"],
        description=data["description"],
        questions=questions
    )

    surveys = await load_surveys()
    surveys[survey.survey_id] = survey
    await save_surveys(surveys)

    if not await get_active_survey_id():
        await set_active_survey(survey.survey_id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
        ]]
    )
    await callback.message.answer(
        f"✅ Опрос \"{survey.name}\" успешно создан!\n"
        f"Всего вопросов: {len(questions)}\n\n"
        "Вы можете управлять опросом через меню 'Управление опросами'",
        reply_markup=keyboard
    )
    await state.clear()
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

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Да ✅", callback_data="answer_yes"),
                    InlineKeyboardButton(text="Нет ❌", callback_data="answer_no")
                ],
                [InlineKeyboardButton(text="Свой ответ ✍️", callback_data="answer_custom")]
            ]
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

@dp.callback_query(F.data.startswith("answer_"))
async def process_answer(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        answer_type = callback.data.replace("answer_", "")
        if answer_type == "custom":
            await callback.message.delete()
            await callback.message.answer("Пожалуйста, введите ваш ответ:")
            await callback.answer()
            return

        data = await state.get_data()
        current_question = data.get("current_question", 0)
        survey_id = data.get("survey_id")
        answers = data.get("answers", [])

        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        if not survey:
            await callback.message.answer("Произошла ошибка при загрузке опроса.")
            await state.clear()
            await callback.answer()
            return

        answers.append(f"Да" if answer_type == "yes" else "Нет")
        current_question += 1

        if current_question < len(survey.questions):
            await state.update_data(
                current_question=current_question,
                answers=answers
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Да ✅", callback_data="answer_yes"),
                        InlineKeyboardButton(text="Нет ❌", callback_data="answer_no")
                    ],
                    [InlineKeyboardButton(text="Свой ответ ✍️", callback_data="answer_custom")]
                ]
            )
            await callback.message.delete()
            await callback.message.answer(
                f"Вопрос {current_question + 1} из {len(survey.questions)}:\n\n{survey.questions[current_question]}",
                reply_markup=keyboard
            )
        else:
            user_id = callback.from_user.id
            username = callback.from_user.username or "НетUsername"
            
            survey_results = [
                f"\nПользователь: @{username}, ID: {user_id}_{survey_id}"
            ]
            for q, a in zip(survey.questions, answers):
                survey_results.extend([
                    f"Вопрос: {q}",
                    f"Ответ: {a}"
                ])
            survey_results.append("Опрос завершён\n")
            
            await safe_append_file(Config.DATA_FILE, "\n".join(survey_results))
            
            await callback.message.delete()
            await callback.message.answer(
                "✅ Спасибо за участие в опросе!\n"
                "Ваши ответы сохранены."
            )
            await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при обработке ответа: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
        await state.clear()
    
    await callback.answer()

@dp.message(SurveyState.waiting_for_answer)
async def process_custom_answer(message: Message, state: FSMContext) -> None:
    try:
        answer = message.text.strip()
        if not answer:
            await message.answer("Ответ не может быть пустым. Пожалуйста, введите ваш ответ:")
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
            await message.answer("Произошла ошибка при загрузке опроса.")
            await state.clear()
            return

        answers.append(answer)
        current_question += 1

        if current_question < len(survey.questions):
            await state.update_data(
                current_question=current_question,
                answers=answers
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Да ✅", callback_data="answer_yes"),
                        InlineKeyboardButton(text="Нет ❌", callback_data="answer_no")
                    ],
                    [InlineKeyboardButton(text="Свой ответ ✍️", callback_data="answer_custom")]
                ]
            )
            await message.answer(
                f"Вопрос {current_question + 1} из {len(survey.questions)}:\n\n{survey.questions[current_question]}",
                reply_markup=keyboard
            )
        else:
            user_id = message.from_user.id
            username = message.from_user.username or "НетUsername"
            
            survey_results = [
                f"\nПользователь: @{username}, ID: {user_id}_{survey_id}"
            ]
            for q, a in zip(survey.questions, answers):
                survey_results.extend([
                    f"Вопрос: {q}",
                    f"Ответ: {a}"
                ])
            survey_results.append("Опрос завершён\n")
            
            await safe_append_file(Config.DATA_FILE, "\n".join(survey_results))
            
            await message.answer(
                "✅ Спасибо за участие в опросе!\n"
                "Ваши ответы сохранены."
            )
            await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при обработке пользовательского ответа: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")
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

        buttons = [
            [InlineKeyboardButton(
                text="❌ Деактивировать" if is_active else "✅ Сделать активным",
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
        await callback.message.delete()
        await callback.message.answer(
            f"Управление опросом: {survey.name}\n"
            f"Описание: {survey.description}\n"
            f"Количество вопросов: {len(survey.questions)}\n"
            f"Статус: {'✅ Активный' if is_active else '❌ Неактивный'}",
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
        active_survey_id = await get_active_survey_id()

        if survey_id == active_survey_id:
            await set_active_survey("")
            await callback.answer("Опрос деактивирован.", show_alert=True)
        else:
            await set_active_survey(survey_id)
            surveys = await load_surveys()
            survey = surveys.get(survey_id)
            if survey:
                await callback.answer(f"Опрос '{survey.name}' теперь активен.", show_alert=True)

        await process_manage_surveys(callback)
    except Exception as e:
        logger.error(f"Ошибка при изменении активного опроса: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
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
        await message.answer("У вас нет прав для рассылки сообщений.")
        await state.clear()
        return

    try:
        broadcast_text = message.text.strip()
        if not broadcast_text:
            await message.answer("Сообщение не может быть пустым. Введите текст сообщения:")
            return

        users = set()
        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            for line in content.split('\n'):
                if line.startswith("Пользователь: @"):
                    try:
                        user_id = line.split(", ID: ")[1].split("_")[0]
                        users.add(int(user_id))
                    except:
                        continue

        if not users:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
                ]]
            )
            await message.answer(
                "Нет пользователей для рассылки.",
                reply_markup=keyboard
            )
            await state.clear()
            return

        sent_count = 0
        for user_id in users:
            try:
                await bot.send_message(user_id, broadcast_text)
                sent_count += 1
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Вернуться в админ-панель", callback_data="admin")
            ]]
        )
        await message.answer(
            f"✅ Рассылка завершена!\n"
            f"Сообщение отправлено {sent_count} из {len(users)} пользователей.",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при рассылке: {e}")
        await message.answer("Произошла ошибка при рассылке. Попробуйте позже.")
    
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
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("❌ Опрос не найден.")
            await callback.answer()
            return

        total_users = 0
        completed_users = 0
        answers_data = {}

        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            current_user_id = None
            current_answers = []
            is_completed = False

            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue

                if line.startswith("Пользователь: @"):
                    if current_user_id and is_completed and current_answers:
                        completed_users += 1
                        for q, a in current_answers:
                            answers_data[q] = answers_data.get(q, {})
                            answers_data[q][a] = answers_data[q].get(a, 0) + 1

                    try:
                        user_info = line.split(", ID: ")[1]
                        current_survey = user_info.split("_")[1]
                        if current_survey == survey_id:
                            total_users += 1
                            current_user_id = True
                            current_answers = []
                            is_completed = False
                        else:
                            current_user_id = None
                    except:
                        current_user_id = None

                elif current_user_id:
                    if line.startswith("Вопрос: "):
                        question = line.replace("Вопрос: ", "")
                    elif line.startswith("Ответ: "):
                        answer = line.replace("Ответ: ", "")
                        current_answers.append((question, answer))
                    elif line == "Опрос завершён":
                        is_completed = True

            if current_user_id and is_completed and current_answers:
                completed_users += 1
                for q, a in current_answers:
                    answers_data[q] = answers_data.get(q, {})
                    answers_data[q][a] = answers_data[q].get(a, 0) + 1

        completion_percentage = (completed_users/total_users*100) if total_users > 0 else 0
        
        stats_text = [
            f"📊 Статистика опроса \"{survey.name}\"",
            f"\n📌 Общая информация:",
            f"├─ 👥 Всего участников: {total_users}",
            f"├─ ✅ Завершили опрос: {completed_users}",
            f"└─ 📈 Процент завершения: {completion_percentage:.1f}%"
        ]

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад к списку опросов", callback_data="view_stats")
            ]]
        )

        message = "\n".join(stats_text)
        await callback.message.delete()
        await callback.message.answer(message, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка при показе статистики опроса: {e}")
        await callback.message.answer("❌ Произошла ошибка при получении статистики. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data == "view_user_answers")
async def process_view_user_answers(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра ответов.", show_alert=True)
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
                "Нет созданных опросов для просмотра ответов.",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons = []
        for survey_id, survey in surveys.items():
            buttons.append([
                InlineKeyboardButton(
                    text=f"📋 {survey.name}",
                    callback_data=f"view_answers_survey_{survey_id}"
                )
            ])

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
        logger.error(f"Ошибка при отображении списка опросов: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("view_answers_survey_"))
async def process_view_survey_users(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра ответов.", show_alert=True)
        return

    try:
        survey_id = callback.data.replace("view_answers_survey_", "")
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            await callback.message.answer("Опрос не найден.")
            await callback.answer()
            return

        users = {}
        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            current_user_id = None
            current_username = None
            is_completed = False

            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue

                if line.startswith("Пользователь: @"):
                    if current_user_id and is_completed:
                        users[current_user_id] = current_username

                    try:
                        username = line.split(", ID: ")[0].replace("Пользователь: @", "")
                        user_info = line.split(", ID: ")[1]
                        user_id, current_survey = user_info.split("_")
                        if current_survey == survey_id:
                            current_user_id = user_id
                            current_username = username
                            is_completed = False
                        else:
                            current_user_id = None
                            current_username = None
                    except:
                        current_user_id = None
                        current_username = None

                elif line == "Опрос завершён" and current_user_id:
                    is_completed = True

            if current_user_id and is_completed:
                users[current_user_id] = current_username

        if not users:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад к списку опросов", callback_data="view_user_answers")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer(
                f"Нет пользователей, прошедших опрос \"{survey.name}\"",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        buttons = []
        for user_id, username in users.items():
            display_name = f"@{username}" if username != "НетUsername" else f"ID: {user_id}"
            buttons.append([
                InlineKeyboardButton(
                    text=display_name,
                    callback_data=f"user_answers_{user_id}_{survey_id}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад к списку опросов", callback_data="view_user_answers")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.delete()
        await callback.message.answer(
            f"Выберите пользователя для просмотра ответов в опросе \"{survey.name}\":",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при отображении списка пользователей: {e}")
        await callback.message.answer("Произошла ошибка. Попробуйте позже.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("user_answers_"))
async def process_user_answers(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра ответов.", show_alert=True)
        return

    try:
        data_parts = callback.data.split("_")
        if len(data_parts) != 4:
            logger.error(f"Неверный формат callback data: {callback.data}")
            raise ValueError("Неверный формат данных")
            
        user_id = data_parts[2]
        survey_id = data_parts[3]
        
        logger.info(f"Получение ответов для пользователя {user_id} в опросе {survey_id}")
        
        surveys = await load_surveys()
        survey = surveys.get(survey_id)
        
        if not survey:
            logger.error(f"Опрос {survey_id} не найден")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад к списку пользователей", callback_data=f"view_answers_survey_{survey_id}")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer("Опрос не найден.", reply_markup=keyboard)
            await callback.answer()
            return

        user_answers = []
        username = None
        
        if not os.path.exists(Config.DATA_FILE):
            logger.error("Файл с ответами пользователей не найден")
            raise FileNotFoundError("Файл с ответами не найден")

        content = await safe_read_file(Config.DATA_FILE)
        if not content:
            logger.error("Файл с ответами пользователей пуст")
            raise ValueError("Файл с ответами пуст")

        is_target_user = False
        current_question = None
        
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith("Пользователь: @"):
                try:
                    current_username = line.split(", ID: ")[0].replace("Пользователь: @", "")
                    user_info = line.split(", ID: ")[1]
                    current_user_id, current_survey = user_info.split("_")
                    
                    if current_user_id == user_id and current_survey == survey_id:
                        is_target_user = True
                        username = current_username
                        user_answers = []
                        logger.info(f"Найден пользователь {username} ({user_id})")
                    else:
                        is_target_user = False
                except Exception as e:
                    logger.error(f"Ошибка при разборе строки пользователя: {e}")
                    is_target_user = False

            elif is_target_user:
                if line.startswith("Вопрос: "):
                    current_question = line.replace("Вопрос: ", "")
                elif line.startswith("Ответ: ") and current_question:
                    answer = line.replace("Ответ: ", "")
                    user_answers.append((current_question, answer))

        if not user_answers:
            logger.warning(f"Ответы не найдены для пользователя {user_id} в опросе {survey_id}")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад к списку пользователей", callback_data=f"view_answers_survey_{survey_id}")
                ]]
            )
            await callback.message.delete()
            await callback.message.answer(
                "Ответы пользователя не найдены.",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        display_name = f"@{username}" if username != "НетUsername" else f"ID: {user_id}"
        message_lines = [
            f"📋 Ответы пользователя {display_name}",
            f"Опрос: {survey.name}\n"
        ]

        for question, answer in user_answers:
            message_lines.extend([
                f"Вопрос: {question}",
                f"Ответ: {answer}\n"
            ])

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад к списку пользователей", callback_data=f"view_answers_survey_{survey_id}")
            ]]
        )

        message = "\n".join(message_lines)
        await callback.message.delete()
        
        if len(message) > 4096:
            for i in range(0, len(message), 4096):
                part = message[i:i+4096]
                if i + 4096 >= len(message):
                    await callback.message.answer(part, reply_markup=keyboard)
                else:
                    await callback.message.answer(part)
        else:
            await callback.message.answer(message, reply_markup=keyboard)
        
        logger.info(f"Успешно отправлены ответы пользователя {user_id} для опроса {survey_id}")

    except FileNotFoundError as e:
        logger.error(f"Файл не найден: {e}")
        await callback.message.answer("Файл с ответами не найден. Попробуйте позже.")
    except ValueError as e:
        logger.error(f"Ошибка значения: {e}")
        await callback.message.answer("Неверный формат данных. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка при показе ответов пользователя: {e}")
        await callback.message.answer("Произошла ошибка при получении ответов. Попробуйте позже.")
    
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
        
        survey_data = [
            f"Данные опроса: {survey.name}",
            f"Описание: {survey.description}",
            f"ID опроса: {survey_id}",
            "\nВопросы:",
        ]
        for i, question in enumerate(survey.questions, 1):
            survey_data.append(f"{i}. {question}")
        
        survey_data.append("\nОтветы пользователей:")
        
        if os.path.exists(Config.DATA_FILE):
            content = await safe_read_file(Config.DATA_FILE)
            current_user_data = []
            
            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue
                
                if line.startswith("Пользователь: @"):
                    if current_user_data:
                        survey_data.extend(current_user_data)
                        current_user_data = []
                    
                    try:
                        user_info = line.split(", ID: ")[1]
                        current_survey = user_info.split("_")[1]
                        if current_survey == survey_id:
                            current_user_data.append(f"\n{line}")
                        else:
                            current_user_data = []
                    except:
                        current_user_data = []
                        
                elif current_user_data:
                    current_user_data.append(line)
            
            if current_user_data:
                survey_data.extend(current_user_data)

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
        
        all_data = ["ДАННЫЕ ВСЕХ ОПРОСОВ\n"]
        
        for survey_id, survey in surveys.items():
            all_data.extend([
                f"\n{'='*50}",
                f"Опрос: {survey.name}",
                f"Описание: {survey.description}",
                f"ID опроса: {survey_id}",
                "\nВопросы:"
            ])
            
            for i, question in enumerate(survey.questions, 1):
                all_data.append(f"{i}. {question}")
            
            all_data.append("\nОтветы пользователей:")
            
            if os.path.exists(Config.DATA_FILE):
                content = await safe_read_file(Config.DATA_FILE)
                current_user_data = []
                
                for line in content.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    
                    if line.startswith("Пользователь: @"):
                        if current_user_data:
                            all_data.extend(current_user_data)
                            current_user_data = []
                        
                        try:
                            user_info = line.split(", ID: ")[1]
                            current_survey = user_info.split("_")[1]
                            if current_survey == survey_id:
                                current_user_data.append(f"\n{line}")
                            else:
                                current_user_data = []
                        except:
                            current_user_data = []
                            
                    elif current_user_data:
                        current_user_data.append(line)
                
                if current_user_data:
                    all_data.extend(current_user_data)

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

async def main() -> None:
    try:
        logger.info("Запуск бота...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 