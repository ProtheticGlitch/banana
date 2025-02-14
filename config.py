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
    
    @classmethod
    def validate_config(cls):
        """Проверка корректности конфигурации"""
        assert cls.API_TOKEN and isinstance(cls.API_TOKEN, str), "API_TOKEN должен быть строкой"
        assert cls.ADMIN_IDS and isinstance(cls.ADMIN_IDS, list), "ADMIN_IDS должен быть списком"
        assert all(isinstance(admin_id, int) for admin_id in cls.ADMIN_IDS), "Все ID администраторов должны быть целыми числами"
        assert cls.FILE_ENCODING, "Необходимо указать кодировку файлов"
        assert cls.MAX_SURVEY_NAME_LENGTH > cls.MIN_SURVEY_NAME_LENGTH, "Некорректные ограничения длины названия"
        assert cls.MAX_SURVEY_DESCRIPTION_LENGTH > cls.MIN_SURVEY_DESCRIPTION_LENGTH, "Некорректные ограничения длины описания"
        assert cls.MAX_QUESTIONS > cls.MIN_QUESTIONS, "Некорректные ограничения количества вопросов" 