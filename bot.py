import json
import os
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio

API_TOKEN = ""

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DATA_DIR = "user_data"
os.makedirs(DATA_DIR, exist_ok=True)


@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    user_data = {"user_id": user_id, "responses": []}
    file_path = os.path.join(DATA_DIR, f"{user_id}.json")

    if not os.path.exists(file_path):
        with open(file_path, 'w') as file:
            json.dump(user_data, file)
        await message.answer("Привет! Мы будем задавать тебе несколько вопросов.")
    else:
        await message.answer("С возвращением! Давай продолжим наш опрос.")

    await send_question(message.chat.id)


async def send_question(chat_id):
    question = "Работаешь ли ты официально?"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="yes"),
                InlineKeyboardButton(text="Нет", callback_data="no")
            ]
        ]
    )
    await bot.send_message(chat_id, question, reply_markup=keyboard)


@dp.callback_query()
async def process_answer(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    answer = "Да" if callback_query.data == "yes" else "Нет"
    file_path = os.path.join(DATA_DIR, f"{user_id}.json")

    if os.path.exists(file_path):
        with open(file_path, 'r') as file:
            user_data = json.load(file)
        user_data["responses"].append({
            "question": "Работаешь ли ты официально?",
            "answer": answer
        })
        with open(file_path, 'w') as file:
            json.dump(user_data, file, ensure_ascii=False, indent=4)
        await callback_query.answer("Ответ сохранен!")
        await callback_query.message.answer("Спасибо за ответ!")
    else:
        await callback_query.answer("Ошибка: пользователь не найден.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())