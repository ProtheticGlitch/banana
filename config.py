import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_TOKEN = os.getenv('BOT_API_TOKEN', "8197644620:AAF3xqwyhop-4XpMPPhczjPTMldEzs_mnZE")
    ADMIN_IDS = [1467310153, 1456535790, 1710633481]
    FILE_ENCODING = 'utf-8'
    
    # Ограничения
    MAX_MESSAGE_LENGTH = 4096
    MAX_QUESTIONS = 50
    MAX_ANSWER_LENGTH = 1000
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
    
    # Пути
    BOT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "БОТ")
    SURVEYS_DIR = os.path.join(BOT_DIR, "surveys")
    SURVEYS_FILE = os.path.join(SURVEYS_DIR, "surveys.txt")
    ACTIVE_SURVEY_FILE = os.path.join(SURVEYS_DIR, "active_survey.txt")
    DATA_FILE = os.path.join(BOT_DIR, "user_data.txt")
    TEMP_DIR = os.path.join(BOT_DIR, "temp")
    
    # Создаем необходимые директории
    os.makedirs(BOT_DIR, exist_ok=True)
    os.makedirs(SURVEYS_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True) 