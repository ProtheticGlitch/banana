import os
import sys
from dotenv import load_dotenv
from typing import List, Dict, Any
import logging
from pathlib import Path

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot_config.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

def input_token() -> str:
    """Запрос токена у пользователя"""
    while True:
        print("\n=== Настройка бота ===")
        token = input("Введите токен бота: ").strip()
        if ':' in token and len(token.split(':')) == 2:
            return token
        print("❌ Ошибка: Токен должен быть в формате 'числа:буквы-и-цифры'")

def input_admin_ids() -> str:
    """Запрос ID администраторов у пользователя"""
    while True:
        print("\nВведите ID администраторов через запятую")
        admin_ids = input("Например: 123456789, 987654321\n").strip()
        try:
            # Проверяем, что все ID являются числами
            ids = [int(id.strip()) for id in admin_ids.split(',')]
            if all(id > 0 for id in ids):
                return admin_ids
            print("❌ Ошибка: ID должны быть положительными числами")
        except ValueError:
            print("❌ Ошибка: Введите числа, разделенные запятыми")

def create_env_file(token: str = None, admin_ids: str = None):
    """Создание файла .env с настройками"""
    if token is None:
        token = input_token()
    if admin_ids is None:
        admin_ids = input_admin_ids()

    env_content = f"""# Токен вашего бота
API_TOKEN={token}

# ID администраторов (через запятую)
ADMIN_IDS={admin_ids}

# Настройки файлов
FILE_ENCODING=utf-8

# Ограничения
MAX_SURVEYS=10
MAX_QUESTIONS=20
MAX_ANSWER_LENGTH=1000
MIN_SURVEY_NAME_LENGTH=3
MAX_SURVEY_NAME_LENGTH=100
MIN_SURVEY_DESCRIPTION_LENGTH=10
MAX_SURVEY_DESCRIPTION_LENGTH=500
MIN_QUESTIONS=1

# Интервалы
CLEANUP_INTERVAL=3600
ERROR_RETRY_INTERVAL=300

# Rate limiting
RATE_LIMIT_MAX_REQUESTS=5
RATE_LIMIT_WINDOW=60
ADMIN_RATE_LIMIT_MAX_REQUESTS=10
ADMIN_RATE_LIMIT_WINDOW=30
RATE_LIMIT_CLEANUP_TIME=3600"""

    try:
        with open('.env', 'w', encoding='utf-8') as f:
            f.write(env_content)
        print("\n✅ Настройки успешно сохранены в файл .env")
    except Exception as e:
        print(f"\n❌ Ошибка при сохранении настроек: {e}")
        sys.exit(1)

def load_config():
    """Загрузка конфигурации"""
    if not os.path.exists('.env'):
        print("Файл .env не найден. Создаем новый файл конфигурации...")
        create_env_file()
    
    load_dotenv()
    
    # Проверяем наличие необходимых настроек
    token = os.getenv('API_TOKEN')
    admin_ids = os.getenv('ADMIN_IDS')
    
    if not token or not admin_ids:
        print("Отсутствуют необходимые настройки. Требуется повторная конфигурация.")
        create_env_file(token, admin_ids)
        load_dotenv()  # Перезагружаем настройки

# Загружаем конфигурацию при импорте модуля
load_config()

class Config:
    # Токен бота
    API_TOKEN = os.getenv('API_TOKEN')
    if not API_TOKEN:
        raise ValueError("API_TOKEN не установлен в .env файле")
    
    # ID администраторов
    ADMIN_IDS = []
    try:
        admin_ids_str = os.getenv('ADMIN_IDS', '')
        if admin_ids_str:
            ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(',') if id.strip()]
    except ValueError as e:
        logger.error(f"Ошибка при парсинге ADMIN_IDS: {e}")
        print("❌ Ошибка в списке администраторов. Пожалуйста, исправьте файл .env")
        sys.exit(1)
    
    # Настройки файлов
    FILE_ENCODING = os.getenv('FILE_ENCODING', 'utf-8')
    
    # Пути к файлам и директориям
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_FILE = os.path.join(BASE_DIR, "user_data.txt")
    
    # Ограничения
    MAX_SURVEYS = int(os.getenv('MAX_SURVEYS', '10'))
    MAX_QUESTIONS = int(os.getenv('MAX_QUESTIONS', '20'))
    MAX_ANSWER_LENGTH = int(os.getenv('MAX_ANSWER_LENGTH', '1000'))
    MIN_SURVEY_NAME_LENGTH = int(os.getenv('MIN_SURVEY_NAME_LENGTH', '3'))
    MAX_SURVEY_NAME_LENGTH = int(os.getenv('MAX_SURVEY_NAME_LENGTH', '100'))
    MIN_SURVEY_DESCRIPTION_LENGTH = int(os.getenv('MIN_SURVEY_DESCRIPTION_LENGTH', '10'))
    MAX_SURVEY_DESCRIPTION_LENGTH = int(os.getenv('MAX_SURVEY_DESCRIPTION_LENGTH', '500'))
    MIN_QUESTIONS = int(os.getenv('MIN_QUESTIONS', '1'))
    
    # Интервалы
    CLEANUP_INTERVAL = int(os.getenv('CLEANUP_INTERVAL', '3600'))
    ERROR_RETRY_INTERVAL = int(os.getenv('ERROR_RETRY_INTERVAL', '300'))
    RATE_LIMIT_MAX_REQUESTS = int(os.getenv('RATE_LIMIT_MAX_REQUESTS', '5'))
    RATE_LIMIT_WINDOW = int(os.getenv('RATE_LIMIT_WINDOW', '60'))
    ADMIN_RATE_LIMIT_MAX_REQUESTS = int(os.getenv('ADMIN_RATE_LIMIT_MAX_REQUESTS', '10'))
    ADMIN_RATE_LIMIT_WINDOW = int(os.getenv('ADMIN_RATE_LIMIT_WINDOW', '30'))
    RATE_LIMIT_CLEANUP_TIME = int(os.getenv('RATE_LIMIT_CLEANUP_TIME', '3600'))

    @classmethod
    def validate_config(cls) -> None:
        """Проверка конфигурации при запуске"""
        if not cls.API_TOKEN:
            print("❌ Ошибка: API_TOKEN не установлен")
            create_env_file()
            sys.exit(1)
        
        if not cls.ADMIN_IDS:
            print("❌ Ошибка: Не указаны ID администраторов")
            create_env_file()
            sys.exit(1)

    @classmethod
    def get_config_dict(cls) -> Dict[str, Any]:
        """Получение всех настроек в виде словаря для отладки"""
        return {
            'API_TOKEN': bool(cls.API_TOKEN),  # Не показываем сам токен
            'ADMIN_IDS': cls.ADMIN_IDS,
            'FILE_ENCODING': cls.FILE_ENCODING,
            'BASE_DIR': cls.BASE_DIR,
            'DATA_FILE': cls.DATA_FILE,
            'MAX_SURVEYS': cls.MAX_SURVEYS,
            'MAX_QUESTIONS': cls.MAX_QUESTIONS,
            'MAX_ANSWER_LENGTH': cls.MAX_ANSWER_LENGTH,
            'MIN_SURVEY_NAME_LENGTH': cls.MIN_SURVEY_NAME_LENGTH,
            'MAX_SURVEY_NAME_LENGTH': cls.MAX_SURVEY_NAME_LENGTH,
            'MIN_SURVEY_DESCRIPTION_LENGTH': cls.MIN_SURVEY_DESCRIPTION_LENGTH,
            'MAX_SURVEY_DESCRIPTION_LENGTH': cls.MAX_SURVEY_DESCRIPTION_LENGTH,
            'MIN_QUESTIONS': cls.MIN_QUESTIONS,
            'CLEANUP_INTERVAL': cls.CLEANUP_INTERVAL,
            'ERROR_RETRY_INTERVAL': cls.ERROR_RETRY_INTERVAL,
            'RATE_LIMIT_MAX_REQUESTS': cls.RATE_LIMIT_MAX_REQUESTS,
            'RATE_LIMIT_WINDOW': cls.RATE_LIMIT_WINDOW,
            'ADMIN_RATE_LIMIT_MAX_REQUESTS': cls.ADMIN_RATE_LIMIT_MAX_REQUESTS,
            'ADMIN_RATE_LIMIT_WINDOW': cls.ADMIN_RATE_LIMIT_WINDOW,
            'RATE_LIMIT_CLEANUP_TIME': cls.RATE_LIMIT_CLEANUP_TIME
        }

    @classmethod
    def validate_config(cls) -> None:
        """Проверка конфигурации при запуске"""
        errors = []
        
        # Проверка критических параметров
        if not cls.API_TOKEN:
            errors.append("API_TOKEN не установлен в .env файле")
        
        if not cls.ADMIN_IDS:
            errors.append("ADMIN_IDS не установлены в .env файле")
        
        # Проверка директорий
        if not os.path.exists(cls.BASE_DIR):
            try:
                os.makedirs(cls.BASE_DIR)
                logger.info(f"Создана директория: {cls.BASE_DIR}")
            except Exception as e:
                errors.append(f"Не удалось создать BASE_DIR: {e}")

        # Проверка типов и значений
        try:
            assert cls.API_TOKEN and isinstance(cls.API_TOKEN, str), "API_TOKEN должен быть строкой"
            assert cls.ADMIN_IDS and isinstance(cls.ADMIN_IDS, list), "ADMIN_IDS должен быть списком"
            assert all(isinstance(admin_id, int) for admin_id in cls.ADMIN_IDS), "Все ID администраторов должны быть целыми числами"
            assert cls.FILE_ENCODING, "Необходимо указать кодировку файлов"
            
            # Проверка ограничений
            assert cls.MAX_SURVEY_NAME_LENGTH > cls.MIN_SURVEY_NAME_LENGTH, "Некорректные ограничения длины названия"
            assert cls.MAX_SURVEY_DESCRIPTION_LENGTH > cls.MIN_SURVEY_DESCRIPTION_LENGTH, "Некорректные ограничения длины описания"
            assert cls.MAX_QUESTIONS > cls.MIN_QUESTIONS, "Некорректные ограничения количества вопросов"
            assert cls.MAX_SURVEYS > 0, "MAX_SURVEYS должно быть больше 0"
            assert cls.MAX_ANSWER_LENGTH > 0, "MAX_ANSWER_LENGTH должно быть больше 0"
            
            # Проверка интервалов
            assert cls.CLEANUP_INTERVAL > 0, "CLEANUP_INTERVAL должен быть больше 0"
            assert cls.ERROR_RETRY_INTERVAL > 0, "ERROR_RETRY_INTERVAL должен быть больше 0"
            assert cls.RATE_LIMIT_WINDOW > 0, "RATE_LIMIT_WINDOW должен быть больше 0"
            assert cls.RATE_LIMIT_MAX_REQUESTS > 0, "RATE_LIMIT_MAX_REQUESTS должен быть больше 0"
            assert cls.ADMIN_RATE_LIMIT_WINDOW > 0, "ADMIN_RATE_LIMIT_WINDOW должен быть больше 0"
            assert cls.ADMIN_RATE_LIMIT_MAX_REQUESTS > 0, "ADMIN_RATE_LIMIT_MAX_REQUESTS должен быть больше 0"
            
        except AssertionError as e:
            errors.append(str(e))

        if errors:
            error_msg = "\n".join(errors)
            logger.error(f"Ошибки конфигурации:\n{error_msg}")
            raise ValueError(error_msg)
        
        # Логируем успешную валидацию
        logger.info("Конфигурация успешно провалидирована")
        config_dict = cls.get_config_dict()
        logger.debug(f"Текущие настройки:\n{config_dict}") 