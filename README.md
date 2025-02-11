# Telegram Bot для Опросов 📊

Этот бот позволяет создавать и проводить опросы через Telegram.

## Возможности 🚀

- Создание опросов с неограниченным количеством вопросов
- Управление активными опросами
- Просмотр статистики и ответов пользователей
- Рассылка сообщений всем пользователям
- Скачивание результатов опросов

## Установка ⚙️

1. Установите Python 3.7 или выше
2. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
3. Создайте файл `.env` в корневой директории и добавьте:
   ```
   BOT_API_TOKEN=ваш_токен_бота
   ADMIN_IDS=id1,id2,id3
   ```

## Запуск ▶️

```bash
python bot.py
```

## Структура проекта 📂

- `bot.py` - основной файл бота
- `config.py` - конфигурация
- `utils.py` - вспомогательные функции
- `requirements.txt` - зависимости
- `.env` - конфигурация окружения (не включена в репозиторий)

## Администрирование 🛠️

Для доступа к админ-панели используйте команду `/admin`.

Доступные функции:
- Создание новых опросов
- Управление существующими опросами
- Просмотр статистики
- Просмотр ответов пользователей
- Скачивание данных
- Рассылка сообщений

## Безопасность 🔒

- Все операции с файлами выполняются безопасно
- Есть ограничения на размер сообщений и файлов
- Периодическая очистка временных файлов
- Проверка свободного места на диске

## Обработка ошибок ⚠️

Бот имеет встроенную систему логирования и обработки ошибок. Логи сохраняются в файл `bot.log`.

## Ссылка на бота 🤖

Попробуйте нашего бота: [@smngr_bot](https://t.me/smngr_bot)
