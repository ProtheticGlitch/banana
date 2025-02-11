import os
import time
import logging
import asyncio
from typing import List, Dict
import aiofiles
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.dispatcher.middlewares.base import BaseMiddleware

FILE_ENCODING = "utf-8"
API_TOKEN = ""
ADMIN_IDS = [,]

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

desktop_path: str = os.path.join(os.path.expanduser("~"), "Desktop")
bot_dir: str = os.path.join(desktop_path, "БОТ")
os.makedirs(bot_dir, exist_ok=True)
QUESTIONS_FILE: str = os.path.join(bot_dir, "questions.txt")
DATA_FILE: str = os.path.join(bot_dir, "user_data.txt")
os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

class ThrottlingMiddleware(BaseMiddleware):
    """
    Ограничивает частоту обновлений от одного пользователя.
    Если разница между текущим сообщением и предыдущим меньше лимита, сообщение не обрабатывается.
    """
    def __init__(self, limit: float = 1.0):
        super().__init__()
        self.limit = limit
        self.last_times: Dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user_id = None
        if isinstance(event, types.Message):
            user_id = event.from_user.id
        elif isinstance(event, types.CallbackQuery):
            user_id = event.from_user.id

        if user_id is not None:
            now = time.monotonic()
            last = self.last_times.get(user_id, 0)
            if now - last < self.limit:
                try:
                    if isinstance(event, types.Message):
                        await event.answer("Слишком много сообщений, пожалуйста, замедлитесь.")
                    elif isinstance(event, types.CallbackQuery):
                        await event.answer("Слишком много запросов, пожалуйста, замедлитесь.", show_alert=True)
                except Exception:
                    pass
                raise BaseMiddleware()
            self.last_times[user_id] = now
        return await handler(event, data)

dp.update.middleware.register(ThrottlingMiddleware(limit=1.0))

async def async_append_to_file(file_path: str, text: str) -> None:
    async with aiofiles.open(file_path, mode='a', encoding=FILE_ENCODING) as f:
        await f.write(text)


async def async_rewrite_file(file_path: str, lines: List[str]) -> None:
    async with aiofiles.open(file_path, mode='w', encoding=FILE_ENCODING) as f:
        await f.write("\n".join(lines) + "\n")


async def async_read_file_lines(file_path: str) -> List[str]:
    if not os.path.exists(file_path):
        return []
    async with aiofiles.open(file_path, mode='r', encoding=FILE_ENCODING) as f:
        content = await f.read()
        return content.splitlines()

class SurveyState(StatesGroup):
    waiting_for_answer = State()


class AddQuestionState(StatesGroup):
    waiting_for_question = State()


class BroadcastState(StatesGroup):
    waiting_for_message = State()

def load_questions() -> List[str]:
    """
    Синхронная загрузка списка вопросов.
    Предполагается, что файл с вопросами небольшой, поэтому блокирующее чтение не критично.
    """
    if not os.path.exists(QUESTIONS_FILE):
        return []
    with open(QUESTIONS_FILE, "r", encoding=FILE_ENCODING) as file:
        questions = [line.strip() for line in file if line.strip()]
    logger.info("Вопросы успешно загружены.")
    return questions


async def save_question(question: str) -> None:
    """Добавляет новый вопрос в файл."""
    await async_append_to_file(QUESTIONS_FILE, question + "\n")
    logger.info(f"Вопрос добавлен: {question}")


async def rewrite_questions(questions_list: List[str]) -> None:
    """Перезаписывает файл вопросов на основе переданного списка."""
    await async_rewrite_file(QUESTIONS_FILE, questions_list)
    logger.info("Файл вопросов обновлён.")


async def get_unique_users() -> Dict[str, str]:
    """
    Возвращает словарь уникальных пользователей, где ключ — ID пользователя (строка), а значение — username.
    Используется для определения, кто уже начинал опрос.
    """
    users: Dict[str, str] = {}
    try:
        lines = await async_read_file_lines(DATA_FILE)
        for line in lines:
            line = line.strip()
            if line.startswith("Пользователь: @"):
                parts = line.split(', ')
                if len(parts) >= 2:
                    username = parts[0].split('@')[1]
                    user_id = parts[1].split(': ')[1]
                    if user_id not in users:
                        users[user_id] = username
    except FileNotFoundError:
        logger.warning("Файл с данными пользователей не найден.")
    return users


async def has_user_taken_survey(user_id: int) -> bool:
    """
    Проверяет, начинал ли пользователь опрос.
    Возвращает True, если ID пользователя уже есть в файле с данными.
    """
    users = await get_unique_users()
    return str(user_id) in users


async def get_survey_statistics() -> Dict[str, Dict]:
    """
    Собирает статистику по участникам опроса.
    Для каждого пользователя собирается:
      - username
      - количество ответов (строки, начинающиеся с "Вопрос:")
      - статус завершения опроса (если в блоке есть строка "Опрос завершён")
    Возвращает словарь вида:
      { user_id: {"username": ..., "answers": <int>, "completed": <bool>} , ... }
    """
    stats: Dict[str, Dict] = {}
    current_user = None
    try:
        lines = await async_read_file_lines(DATA_FILE)
        for line in lines:
            line = line.strip()
            if line.startswith("Пользователь: @"):
                parts = line.split(', ')
                if len(parts) >= 2:
                    username = parts[0].split('@')[1]
                    user_id = parts[1].split(': ')[1]
                    current_user = user_id
                    stats[current_user] = {"username": username, "answers": 0, "completed": False}
            else:
                if current_user is None:
                    continue
                if line.startswith("Вопрос:"):
                    stats[current_user]["answers"] += 1
                elif line == "Опрос завершён":
                    stats[current_user]["completed"] = True
    except FileNotFoundError:
        logger.warning("Файл с данными пользователей не найден для сбора статистики.")
    return stats

QUESTIONS: List[str] = load_questions()


def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором."""
    return user_id in ADMIN_IDS

@dp.message(Command("start"))
async def start(message: types.Message) -> None:
    """
    Обрабатывает команду /start. Отправляет приветственное сообщение с главным меню.
    """
    logger.info(f"Получено сообщение от {message.from_user.id}: {message.text}")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начать опрос 📝", callback_data="start_survey")],
        ]
    )
    await message.answer(
        "Привет! Выберите действие ниже: 👇\n\n"
        "📝 Начать опрос — начать новый опрос.\n",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "start_survey")
async def start_survey(callback_query: types.CallbackQuery) -> None:
    """
    Начинает опрос: сохраняет данные пользователя и отправляет первый вопрос.
    Пользователь может пройти опрос только один раз.
    """
    user_id = callback_query.from_user.id

    if await has_user_taken_survey(user_id):
        await callback_query.answer("Вы уже проходили опрос!", show_alert=True)
        return

    user_username = callback_query.from_user.username or "НетUsername"
    await async_append_to_file(DATA_FILE, f"\nПользователь: @{user_username}, ID: {user_id}\n")
    await callback_query.message.answer("📢 Начинаем опрос!")
    await send_question(callback_query.message.chat.id, 0)
    await callback_query.answer()


async def send_question(chat_id: int, question_index: int) -> None:
    """
    Отправляет вопрос с заданным индексом или сообщение о завершении опроса.
    При завершении опроса добавляется строка "Опрос завершён" в файл.
    """
    if question_index < len(QUESTIONS):
        question = QUESTIONS[question_index]
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Да", callback_data=f"answer_{question_index}_yes"),
                    InlineKeyboardButton(text="Нет", callback_data=f"answer_{question_index}_no"),
                    InlineKeyboardButton(text="Ваш ответ", callback_data=f"answer_{question_index}_custom")
                ]
            ]
        )
        await bot.send_message(chat_id, question, reply_markup=keyboard)
    else:
        await async_append_to_file(DATA_FILE, "Опрос завершён\n")
        await bot.send_message(chat_id, "✅ Опрос завершён. Спасибо за участие!")


@dp.callback_query(F.data.startswith("answer_"))
async def process_answer(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """
    Обрабатывает ответы на вопросы (да/нет или выбор текстового ответа).
    """
    data = callback_query.data.split("_")
    try:
        question_index = int(data[1])
    except ValueError:
        await callback_query.answer("Ошибка в данных ответа.")
        return

    if data[2] == "custom":
        await callback_query.message.answer("Пожалуйста, введите ваш ответ:")
        await state.set_state(SurveyState.waiting_for_answer)
        await state.update_data(question_index=question_index)
        await callback_query.answer()
    else:
        answer = "Да" if data[2] == "yes" else "Нет"
        await async_append_to_file(DATA_FILE, f"Вопрос: {QUESTIONS[question_index]} — Ответ: {answer}\n")
        logger.info(
            f"Ответ сохранён для пользователя {callback_query.from_user.id}. "
            f"Вопрос {question_index}. Ответ: {answer}"
        )
        await callback_query.answer("✅ Ответ сохранён!")
        await send_question(callback_query.message.chat.id, question_index + 1)


@dp.message(SurveyState.waiting_for_answer)
async def process_custom_answer(message: types.Message, state: FSMContext) -> None:
    """
    Обрабатывает текстовый ответ пользователя, сохраняет его и отправляет следующий вопрос.
    """
    custom_answer = message.text.strip()
    user_data = await state.get_data()
    question_index = user_data.get('question_index')
    if not custom_answer:
        await message.answer("Ответ не может быть пустым. Пожалуйста, введите ваш ответ.")
        return

    await async_append_to_file(DATA_FILE, f"Вопрос: {QUESTIONS[question_index]} — Ответ: {custom_answer}\n")
    logger.info(
        f"Текстовый ответ сохранён для пользователя {message.from_user.id}. "
        f"Вопрос {question_index}. Ответ: {custom_answer}"
    )
    await message.answer(f"✅ Ваш ответ сохранён: {custom_answer}")
    await send_question(message.chat.id, question_index + 1)
    await state.clear()

@dp.message(Command("admin"))
async def admin_panel(message: types.Message) -> None:
    """
    Отправляет меню администрирования.
    """
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав для доступа к админ-панели.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить вопрос 📝", callback_data="add_question")],
            [InlineKeyboardButton(text="Удалить вопрос ❌", callback_data="delete_question_list")],
            [InlineKeyboardButton(text="Просмотр вопросов 👀", callback_data="view_questions")],
            [InlineKeyboardButton(text="Просмотреть ответы пользователя 🔍", callback_data="view_user_answers")],
            [InlineKeyboardButton(text="Рассылка сообщения 📢", callback_data="broadcast_all")],
            [InlineKeyboardButton(text="Статистика 📊", callback_data="survey_stats")]
        ]
    )
    await message.answer("👨‍💻 Добро пожаловать в админ-панель. Выберите действие:", reply_markup=keyboard)


@dp.callback_query(F.data == "add_question")
async def add_question_callback(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """
    Запрашивает у администратора ввод нового вопроса.
    """
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("Нет прав для доступа.", show_alert=True)
        return
    await callback_query.message.answer("Введите текст нового вопроса:\nПример: \"Вам нравится кофе?\"")
    await state.set_state(AddQuestionState.waiting_for_question)
    await callback_query.answer()


@dp.message(AddQuestionState.waiting_for_question)
async def process_new_question(message: types.Message, state: FSMContext) -> None:
    """
    Обрабатывает введённый администратором вопрос и сохраняет его.
    """
    question_text = message.text.strip()
    if not question_text:
        await message.answer("Текст вопроса не должен быть пустым. Попробуйте ещё раз:")
        return
    await save_question(question_text)
    global QUESTIONS
    QUESTIONS = load_questions()
    await message.answer(f"Вопрос успешно добавлен: {question_text}")
    await state.clear()


@dp.callback_query(F.data == "delete_question_list")
async def delete_question_list(callback_query: types.CallbackQuery) -> None:
    """
    Отображает список вопросов для удаления.
    """
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("Нет прав для доступа.", show_alert=True)
        return
    if not QUESTIONS:
        await callback_query.message.answer("Нет вопросов для удаления.")
        await callback_query.answer()
        return

    buttons = []
    for idx, q in enumerate(QUESTIONS):
        display_text = f"{idx + 1}. {q[:30]}{'...' if len(q) > 30 else ''}"
        buttons.append([InlineKeyboardButton(text=display_text, callback_data=f"delete_{idx}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons, row_width=1)
    await callback_query.message.answer("Выберите вопрос для удаления:", reply_markup=keyboard)
    await callback_query.answer()


@dp.callback_query(F.data.startswith("delete_"))
async def delete_question(callback_query: types.CallbackQuery) -> None:
    """
    Удаляет выбранный вопрос.
    """
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("Нет прав для доступа.", show_alert=True)
        return
    try:
        idx = int(callback_query.data.split("_")[1])
    except (IndexError, ValueError):
        await callback_query.answer("Ошибка при определении вопроса для удаления.")
        return

    if idx < 0 or idx >= len(QUESTIONS):
        await callback_query.answer("Неверный номер вопроса.")
        return

    deleted_question = QUESTIONS.pop(idx)
    await rewrite_questions(QUESTIONS)
    await callback_query.message.answer(f"Вопрос удалён: {deleted_question}")
    await callback_query.answer()


@dp.callback_query(F.data == "view_questions")
async def view_questions(callback_query: types.CallbackQuery) -> None:
    """
    Отправляет список всех вопросов администратору.
    """
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("Нет прав для доступа к вопросам.")
        return

    questions_text = "\n".join([f"{idx + 1}. {q}" for idx, q in enumerate(QUESTIONS)])
    if not questions_text:
        questions_text = "Нет вопросов для отображения."
    await callback_query.message.answer(f"📋 Список вопросов:\n\n{questions_text}")
    await callback_query.answer()


@dp.callback_query(F.data == "view_user_answers")
async def view_user_answers(callback_query: types.CallbackQuery) -> None:
    """
    Показывает список пользователей, прошедших опрос, для дальнейшего просмотра их ответов.
    """
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("Нет прав для доступа.", show_alert=True)
        return

    users = await get_unique_users()
    if not users:
        await callback_query.message.answer("Нет данных о пользователях.")
        await callback_query.answer()
        return

    buttons = []
    for user_id, username in users.items():
        button_text = f"@{username}" if username != "НетUsername" else f"ID: {user_id}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"user_{user_id}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback_query.message.answer("Выберите пользователя:", reply_markup=keyboard)
    await callback_query.answer()


@dp.callback_query(F.data.startswith("user_"))
async def show_user_answers(callback_query: types.CallbackQuery) -> None:
    """
    Отправляет администратору ответы выбранного пользователя.
    """
    user_id = callback_query.data.split("_")[1]
    responses = []
    try:
        lines = await async_read_file_lines(DATA_FILE)
        current_user = None
        for line in lines:
            line = line.strip()
            if line.startswith("Пользователь: @"):
                parts = line.split(', ')
                current_user_id = parts[1].split(': ')[1]
                current_user = current_user_id if current_user_id == user_id else None
            elif current_user == user_id and line.startswith("Вопрос:"):
                responses.append(line)
    except Exception as e:
        logger.error(f"Ошибка при чтении ответов пользователя: {e}")
        await callback_query.message.answer("Ошибка при получении ответов.")
        return

    if not responses:
        await callback_query.message.answer("У пользователя нет ответов.")
    else:
        response_text = "\n".join(responses)
        users = await get_unique_users()
        display_id = f"@{users.get(user_id, user_id)}"
        await callback_query.message.answer(
            f"📝 Ответы пользователя ({display_id}):\n\n{response_text}"
        )
    await callback_query.answer()


@dp.callback_query(F.data == "broadcast_all")
async def broadcast_all_callback(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """
    Запрашивает у администратора текст сообщения для рассылки.
    """
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("Нет прав для доступа.", show_alert=True)
        return
    await callback_query.message.answer("Введите текст сообщения для рассылки:")
    await state.set_state(BroadcastState.waiting_for_message)
    await callback_query.answer()


@dp.message(BroadcastState.waiting_for_message)
async def process_broadcast_message(message: types.Message, state: FSMContext) -> None:
    """
    Обрабатывает сообщение для рассылки и отправляет его всем уникальным пользователям.
    """
    broadcast_text = message.text.strip()
    if not broadcast_text:
        await message.answer("Сообщение не может быть пустым. Пожалуйста, введите текст сообщения.")
        return

    users = await get_unique_users()
    if not users:
        await message.answer("Нет пользователей для рассылки.")
        await state.clear()
        return

    sent_count = 0
    for user_id in users.keys():
        try:
            await bot.send_message(int(user_id), broadcast_text)
            sent_count += 1
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")
    await message.answer(f"Рассылка завершена. Сообщение отправлено {sent_count} пользователям.")
    await state.clear()


@dp.callback_query(F.data == "survey_stats")
async def survey_stats(callback_query: types.CallbackQuery) -> None:
    """
    Собирает и отправляет администратору статистику по опросу:
      - Общее число пользователей, начавших опрос
      - Число пользователей, завершивших опрос
      - Детальная информация по каждому участнику, где вместо числового ID выводится @username (если задан)
    """
    stats = await get_survey_statistics()
    total_users = len(stats)
    completed_count = sum(1 for s in stats.values() if s.get("completed"))
    message_lines = [
        "📊 Статистика опроса:",
        f"Всего пользователей: {total_users}",
        f"Завершили опрос: {completed_count}",
        "",
        "Детали по пользователям:"
    ]
    for user_id, data in stats.items():
        display_id = f"@{data['username']}" if data['username'] != "НетUsername" else user_id
        message_lines.append(
            f"ID: {display_id}, Ответов: {data['answers']}, Завершил: {'Да' if data['completed'] else 'Нет'}"
        )
    await callback_query.message.answer("\n".join(message_lines))
    await callback_query.answer()

async def main() -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
