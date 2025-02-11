import os
import time
import shutil
import logging
import asyncio
from typing import Optional, Callable, Any
import aiofiles
from config import Config

logger = logging.getLogger(__name__)

file_lock = asyncio.Lock()

async def safe_file_operation(operation: Callable, *args, **kwargs) -> Any:
    """Безопасное выполнение операций с файлами с использованием блокировки"""
    async with file_lock:
        return await operation(*args, **kwargs)

def check_disk_space(file_path: str, required_bytes: int = 1024*1024) -> bool:
    """Проверка свободного места на диске"""
    try:
        total, used, free = shutil.disk_usage(os.path.dirname(file_path))
        return free > required_bytes
    except Exception as e:
        logger.error(f"Ошибка при проверке места на диске: {e}")
        return False

async def check_file_size(file_path: str) -> bool:
    """Проверка размера файла"""
    try:
        stats = os.stat(file_path)
        return stats.st_size <= Config.MAX_FILE_SIZE
    except Exception as e:
        logger.error(f"Ошибка при проверке размера файла: {e}")
        return False

async def cleanup_temp_files(max_age: int = 3600):
    """Очистка временных файлов старше max_age секунд"""
    try:
        current_time = time.time()
        for filename in os.listdir(Config.TEMP_DIR):
            file_path = os.path.join(Config.TEMP_DIR, filename)
            if os.path.isfile(file_path):
                if current_time - os.path.getctime(file_path) > max_age:
                    os.remove(file_path)
    except Exception as e:
        logger.error(f"Ошибка при очистке временных файлов: {e}")

async def safe_read_file(file_path: str) -> str:
    """Безопасное чтение файла"""
    try:
        if not os.path.exists(file_path):
            return ""
        if not await check_file_size(file_path):
            logger.error(f"Файл {file_path} превышает максимально допустимый размер")
            return ""
        async with aiofiles.open(file_path, 'r', encoding=Config.FILE_ENCODING) as f:
            return await f.read()
    except Exception as e:
        logger.error(f"Ошибка при чтении файла {file_path}: {e}")
        return ""

async def safe_write_file(file_path: str, content: str) -> bool:
    """Безопасная запись в файл"""
    try:
        if not check_disk_space(file_path):
            logger.error(f"Недостаточно места на диске для записи в файл {file_path}")
            return False
            
        # Создаем временный файл
        temp_file = os.path.join(Config.TEMP_DIR, f"temp_{int(time.time())}.txt")
        async with aiofiles.open(temp_file, 'w', encoding=Config.FILE_ENCODING) as f:
            await f.write(content)
            
        # Проверяем размер временного файла
        if not await check_file_size(temp_file):
            os.remove(temp_file)
            logger.error(f"Содержимое превышает максимально допустимый размер файла")
            return False
            
        # Если все проверки пройдены, перемещаем временный файл
        shutil.move(temp_file, file_path)
        return True
    except Exception as e:
        logger.error(f"Ошибка при записи в файл {file_path}: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return False

async def safe_append_file(file_path: str, content: str) -> bool:
    """Безопасное добавление в файл"""
    try:
        if not check_disk_space(file_path):
            logger.error(f"Недостаточно места на диске для добавления в файл {file_path}")
            return False
            
        current_content = await safe_read_file(file_path)
        new_content = current_content + content
        
        return await safe_write_file(file_path, new_content)
    except Exception as e:
        logger.error(f"Ошибка при добавлении в файл {file_path}: {e}")
        return False 