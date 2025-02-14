# Telegram Bot для проведения опросов 🤖

Этот бот предназначен для создания и проведения опросов в Telegram. Он позволяет администраторам создавать различные опросы, управлять ими и собирать ответы пользователей.

**Попробуйте нашего бота: [@smngr_bot](https://t.me/smngr_bot)**

---

## Основные возможности

### Для администраторов
- ✨ Создание новых опросов с настраиваемыми вопросами
- ⚙️ Управление существующими опросами (редактирование, удаление)
- 📊 Просмотр статистики по опросам
- 👥 Просмотр ответов пользователей
- 📥 Выгрузка данных опросов в текстовом формате
- 📢 Рассылка сообщений пользователям

### Для пользователей
- 📝 Прохождение активных опросов
- ✍️ Возможность давать как стандартные, так и свои варианты ответов
- 🔄 Удобный интерфейс с кнопками

## Требования

- Python 3.7+
- aiogram 3.0.0+
- aiofiles 0.8.0+
- python-dotenv 0.19.0+

## Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/ProtheticGlitch/banana
```

2. Создайте виртуальное окружение и активируйте его:
```bash
python -m venv venv
# Для Windows:
venv\Scripts\activate
# Для Linux/Mac:
source venv/bin/activate
```

3. Установите зависимости:
```bash
pip install -r requirements.txt
```

4. Создайте файл `config.py` и настройте его:
```python
class Config:
    # Токен вашего бота, полученный от @BotFather
    API_TOKEN = "your_bot_token_here"
    
    # ID администраторов (можно получить через @userinfobot)
    ADMIN_IDS = [
        123456789,  # Замените на реальные ID администраторов
    ]
    
    # Настройки файлов
    FILE_ENCODING = 'utf-8'
    
    # Ограничения
    MAX_SURVEY_NAME_LENGTH = 100
    MIN_SURVEY_NAME_LENGTH = 3
    MAX_SURVEY_DESCRIPTION_LENGTH = 500
    MIN_SURVEY_DESCRIPTION_LENGTH = 10
    MAX_QUESTIONS = 20
    MIN_QUESTIONS = 1
    MAX_SURVEYS = 10
    MAX_ANSWER_LENGTH = 1000
    
    # Rate limiting
    RATE_LIMIT_MAX_REQUESTS = 5
    RATE_LIMIT_WINDOW = 60
    ADMIN_RATE_LIMIT_MAX_REQUESTS = 20
    ADMIN_RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_CLEANUP_TIME = 3600
    
    # Интервалы
    CLEANUP_INTERVAL = 3600
    ERROR_RETRY_INTERVAL = 300
```

## Запуск бота

1. Убедитесь, что виртуальное окружение активировано
2. Запустите бота:
```bash
python bot.py
```

## Структура проекта

```
├── bot.py              # Основной файл бота
├── config.py           # Конфигурация (не включена в репозиторий)
├── utils.py            # Вспомогательные функции
├── requirements.txt    # Зависимости проекта
└── README.md          # Документация
```

## Команды бота

### Для всех пользователей
- `/start` - Начать работу с ботом / показать список доступных опросов

### Для администраторов
- `/admin` - Доступ к панели администратора

## Безопасность

- Все пользовательские данные проходят санитизацию
- Реализована защита от флуда (rate limiting)
- Доступ к админ-функциям только для авторизованных пользователей
- Периодическая очистка временных файлов
- Ротация логов

## Хранение данных

Бот использует файловую систему для хранения:
- Опросов и их настроек
- Ответов пользователей
- Логов работы

## Лицензия

[MIT License](LICENSE)
