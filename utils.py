import os
import re
import html
import time
import shutil
import secrets
import logging
import asyncio
from typing import Optional, Callable, Any, Dict
from collections import defaultdict
import aiofiles
from config import Config
import string
from datetime import datetime, timedelta
import uuid

logger = logging.getLogger(__name__)

# Глобальные блокировки для файловых операций
file_locks: Dict[str, asyncio.Lock] = {}
rate_limits: Dict[int, Dict[str, Any]] = {}

def get_file_lock(file_path: str) -> asyncio.Lock:
    """Получает или создает блокировку для файла"""
    if file_path not in file_locks:
        file_locks[file_path] = asyncio.Lock()
    return file_locks[file_path]

def check_rate_limit(user_id: int, action_type: str = "default", is_admin: bool = False) -> bool:
    """
    Улучшенная проверка rate limiting с разными ограничениями для разных действий
    """
    current_time = datetime.now()
    if user_id not in rate_limits:
        rate_limits[user_id] = {}
    
    if action_type not in rate_limits[user_id]:
        rate_limits[user_id][action_type] = {
            "last_request": current_time,
            "request_count": 1
        }
        return True

    user_limits = rate_limits[user_id][action_type]
    time_diff = (current_time - user_limits["last_request"]).total_seconds()
    
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

async def safe_file_operation(operation: Callable, file_path: str, *args, **kwargs) -> Any:
    """Безопасное выполнение файловой операции с блокировкой"""
    lock = get_file_lock(file_path)
    async with lock:
        try:
            return await operation(file_path, *args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка при работе с файлом {file_path}: {e}")
            return None

def check_disk_space(min_space_mb: int = 100) -> bool:
    """Проверка свободного места на диске"""
    try:
        total, used, free = os.statvfs('.').f_blocks, os.statvfs('.').f_bfree, os.statvfs('.').f_bavail
        free_mb = (free * os.statvfs('.').f_frsize) / (1024 * 1024)
        return free_mb >= min_space_mb
    except Exception as e:
        logger.error(f"Ошибка при проверке места на диске: {e}")
        return True

def check_file_size(file_path: str, max_size_mb: int = 10) -> bool:
    """Проверка размера файла"""
    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        return size_mb <= max_size_mb
    except Exception as e:
        logger.error(f"Ошибка при проверке размера файла {file_path}: {e}")
        return True

async def cleanup_temp_files() -> None:
    """Очистка временных файлов"""
    try:
        current_time = datetime.now()
        for root, _, files in os.walk("."):
            for file in files:
                if file.startswith("survey_data_") and file.endswith(".txt"):
                    file_path = os.path.join(root, file)
                    file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                    if current_time - file_time > timedelta(hours=24):
                        try:
                            os.remove(file_path)
                            logger.info(f"Удален временный файл: {file_path}")
                        except Exception as e:
                            logger.error(f"Ошибка при удалении файла {file_path}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при очистке временных файлов: {e}")

async def safe_read_file(file_path: str) -> str:
    """Безопасное чтение файла"""
    try:
        if not os.path.exists(file_path):
            return ""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            return await f.read()
    except Exception as e:
        logger.error(f"Ошибка при чтении файла {file_path}: {e}")
        return ""

async def safe_write_file(file_path: str, content: str) -> bool:
    """Безопасная запись в файл"""
    try:
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
        return True
    except Exception as e:
        logger.error(f"Ошибка при записи в файл {file_path}: {e}")
        return False

async def safe_append_file(file_path: str, content: str) -> bool:
    """Безопасное добавление в файл"""
    try:
        async with aiofiles.open(file_path, 'a', encoding='utf-8') as f:
            await f.write(f"\n{content}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении в файл {file_path}: {e}")
        return False

def validate_text_length(text: str, max_length: int, min_length: int = 1) -> bool:
    """Проверка длины текста"""
    if not text:
        return False
    text_length = len(text.strip())
    return min_length <= text_length <= max_length

def validate_questions_count(questions: list) -> bool:
    """Проверка количества вопросов"""
    return Config.MIN_QUESTIONS <= len(questions) <= Config.MAX_QUESTIONS

def sanitize_filename(filename: str) -> str:
    """Санитизация имени файла"""
    # Удаляем недопустимые символы
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Ограничиваем длину
    return filename[:255]

def sanitize_input(text: str) -> str:
    """Санитизация пользовательского ввода"""
    if not text:
        return ""
    # Удаляем управляющие символы
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\t')
    # Ограничиваем длину
    return text[:1000]

def generate_secure_id() -> str:
    """Генерация безопасного идентификатора"""
    return str(uuid.uuid4())

async def create_backup(filepath: str) -> Optional[str]:
    """Создает резервную копию файла"""
    try:
        if not os.path.exists(filepath):
            logger.warning(f"Файл для резервного копирования не существует: {filepath}")
            return None
            
        backup_path = f"{filepath}.bak"
        async with aiofiles.open(filepath, 'rb') as source:
            content = await source.read()
        async with aiofiles.open(backup_path, 'wb') as target:
            await target.write(content)
        logger.info(f"Создана резервная копия: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Ошибка при создании резервной копии {filepath}: {e}")
        return None

async def restore_from_backup(backup_path: str, target_path: str) -> bool:
    """Восстанавливает файл из резервной копии"""
    try:
        if not os.path.exists(backup_path):
            logger.warning(f"Файл резервной копии не существует: {backup_path}")
            return False
            
        async with aiofiles.open(backup_path, 'rb') as source:
            content = await source.read()
        async with aiofiles.open(target_path, 'wb') as target:
            await target.write(content)
        logger.info(f"Файл восстановлен из резервной копии: {target_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при восстановлении из резервной копии {backup_path}: {e}")
        return False