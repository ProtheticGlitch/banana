import os
import shutil
import secrets
import logging
import asyncio
import platform
import tempfile
import hashlib
from typing import Optional, Callable, Any, Dict, List, Tuple, Union, AsyncGenerator
import aiofiles
from config import Config
from datetime import datetime, timedelta
import uuid
import codecs

try:
    import chardet
except ImportError:
    import charset_normalizer as chardet

logger = logging.getLogger(__name__)

# Определение системных констант
SYSTEM_NAME = platform.system().lower()
IS_WINDOWS = SYSTEM_NAME == 'windows'
IS_LINUX = SYSTEM_NAME == 'linux'
IS_MACOS = SYSTEM_NAME == 'darwin'

# Глобальные блокировки для файловых операций
file_locks: Dict[str, asyncio.Lock] = {}
rate_limits: Dict[int, Dict[str, Any]] = {}

def get_temp_dir() -> str:
    """Получение временной директории с учетом ОС"""
    try:
        # Пробуем создать временную директорию в текущей папке
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir
    except:
        # Если не удалось, используем системную временную директорию
        return tempfile.gettempdir()

def get_file_lock(file_path: str) -> asyncio.Lock:
    """Получает или создает блокировку для файла"""
    normalized_path = os.path.normpath(file_path.lower())
    if normalized_path not in file_locks:
        file_locks[normalized_path] = asyncio.Lock()
    return file_locks[normalized_path]

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
    """Безопасное выполнение файловой операции с блокировкой и повторными попытками"""
    lock = get_file_lock(file_path)
    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            async with lock:
                return await operation(file_path, *args, **kwargs)
        except (PermissionError, OSError) as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            logger.error(f"Ошибка доступа к файлу {file_path} после {max_retries} попыток: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при работе с файлом {file_path}: {e}")
            return None

def check_disk_space(min_space_mb: int = 100) -> bool:
    """Проверка свободного места на диске"""
    try:
        if os.name == 'nt':  # Windows
            import ctypes
            free_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p('.'), None, None, ctypes.pointer(free_bytes))
            free_mb = free_bytes.value / (1024 * 1024)
        else:  # Linux/MacOS
            st = os.statvfs('.')
            free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
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
    """Очистка временных файлов с учетом ОС"""
    try:
        temp_dir = get_temp_dir()
        current_time = datetime.now()
        
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if any(file.endswith(ext) for ext in ['.tmp', '.bak', '.txt']):
                    file_path = os.path.join(root, file)
                    try:
                        file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                        if current_time - file_time > timedelta(hours=24):
                            try:
                                os.remove(file_path)
                                logger.info(f"Удален временный файл: {file_path}")
                            except PermissionError:
                                # Для Windows: если файл занят, пропускаем его
                                continue
                    except OSError:
                        continue
    except Exception as e:
        logger.error(f"Ошибка при очистке временных файлов: {e}")

async def safe_read_file(file_path: str) -> str:
    """Безопасное чтение файла с поддержкой всех систем и кодировок"""
    encodings = ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251', 'ascii']
    file_path = os.path.normpath(file_path)  # Нормализация пути для текущей ОС
    
    if not os.path.exists(file_path):
        logger.warning(f"Файл не существует: {file_path}")
        return ""

    for encoding in encodings:
        try:
            async with aiofiles.open(file_path, 'r', encoding=encoding) as f:
                content = await f.read()
                # Нормализация переносов строк
                content = content.replace('\r\n', '\n').replace('\r', '\n')
                return content
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.error(f"Ошибка при чтении файла {file_path} с кодировкой {encoding}: {e}")
            continue

    # Если ни одна кодировка не подошла, пробуем бинарное чтение
    try:
        async with aiofiles.open(file_path, 'rb') as f:
            content = await f.read()
            # Пробуем разные варианты декодирования
            for encoding in encodings:
                try:
                    text = content.decode(encoding)
                    text = text.replace('\r\n', '\n').replace('\r', '\n')
                    return text
                except:
                    continue
            # Если ничего не помогло, используем utf-8 с игнорированием ошибок
            return content.decode('utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"Ошибка при бинарном чтении файла {file_path}: {e}")
        return ""

async def safe_write_file(file_path: str, content: str) -> bool:
    """Безопасная запись файла с поддержкой всех систем"""
    try:
        file_path = os.path.normpath(file_path)  # Нормализация пути
        directory = os.path.dirname(file_path)
        
        # Создаем директории, если их нет
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        # Нормализация переносов строк для текущей ОС
        if os.name == 'nt':  # Windows
            content = content.replace('\n', '\r\n')
        else:  # Linux/MacOS
            content = content.replace('\r\n', '\n')

        # Создаем временный файл
        temp_path = file_path + '.tmp'
        async with aiofiles.open(temp_path, 'w', encoding='utf-8-sig') as f:
            await f.write(content)
        
        # Если файл существует, создаем резервную копию
        if os.path.exists(file_path):
            backup_path = file_path + '.bak'
            try:
                os.replace(file_path, backup_path)
            except Exception as e:
                logger.error(f"Ошибка при создании резервной копии: {e}")

        # Переименовываем временный файл
        os.replace(temp_path, file_path)
        return True
    except Exception as e:
        logger.error(f"Ошибка при записи файла {file_path}: {e}")
        # Пробуем восстановить из резервной копии
        if os.path.exists(backup_path):
            try:
                os.replace(backup_path, file_path)
            except:
                pass
        return False

async def safe_append_file(file_path: str, content: str) -> bool:
    """Безопасное добавление в файл с поддержкой всех систем"""
    try:
        file_path = os.path.normpath(file_path)
        
        # Если файл не существует, создаем его
        if not os.path.exists(file_path):
            return await safe_write_file(file_path, content)
        
        # Определяем текущую кодировку файла
        current_encoding = 'utf-8-sig'
        try:
            with open(file_path, 'rb') as f:
                raw = f.read(4)
                if raw.startswith(b'\xef\xbb\xbf'):  # UTF-8 с BOM
                    current_encoding = 'utf-8-sig'
                elif raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):  # UTF-16
                    current_encoding = 'utf-16'
                else:
                    current_encoding = 'utf-8'
        except:
            pass

        # Нормализация переносов строк
        if os.name == 'nt':  # Windows
            content = content.replace('\n', '\r\n')
        else:  # Linux/MacOS
            content = content.replace('\r\n', '\n')

        # Создаем временный файл
        temp_path = file_path + '.tmp'
        
        # Копируем существующее содержимое
        async with aiofiles.open(file_path, 'r', encoding=current_encoding) as source:
            existing_content = await source.read()
        
        # Записываем всё в временный файл
        async with aiofiles.open(temp_path, 'w', encoding='utf-8-sig') as target:
            await target.write(existing_content)
            if not existing_content.endswith('\n'):
                await target.write('\n')
            await target.write(content)
        
        # Создаем резервную копию
        backup_path = file_path + '.bak'
        try:
            os.replace(file_path, backup_path)
        except Exception as e:
            logger.error(f"Ошибка при создании резервной копии: {e}")
        
        # Переименовываем временный файл
        os.replace(temp_path, file_path)
        return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении в файл {file_path}: {e}")
        # Пробуем восстановить из резервной копии
        if os.path.exists(backup_path):
            try:
                os.replace(backup_path, file_path)
            except:
                pass
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
    """Очистка имени файла для всех ОС"""
    # Заменяем недопустимые символы
    invalid_chars = '<>:"/\\|?*\x00-\x1f'
    filename = ''.join(char if char not in invalid_chars else '_' for char in filename)
    
    # Запрещенные имена в Windows
    forbidden_names = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }
    
    # Проверяем имя файла без расширения
    name, ext = os.path.splitext(filename)
    if name.upper() in forbidden_names:
        name = f"_{name}"
    
    # Убираем точки и пробелы в начале и конце
    name = name.strip('. ')
    if not name:
        name = '_'
    
    # Собираем имя файла обратно
    filename = name + ext
    
    # Ограничиваем длину имени файла (учитываем ограничения всех ОС)
    max_length = 240  # Меньше чем 255 для запаса
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length-len(ext)] + ext
    
    return filename

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

def get_safe_filename(base_name: str) -> str:
    """Создание уникального безопасного имени файла"""
    safe_name = sanitize_filename(base_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_hex(4)
    return f"{safe_name}_{timestamp}_{random_suffix}"

async def ensure_directory(directory: str) -> bool:
    """Безопасное создание директории с обработкой ошибок"""
    try:
        if not directory:
            return False
        
        # Нормализация пути для текущей ОС
        directory = os.path.normpath(directory)
        
        # Проверка на существование
        if os.path.exists(directory):
            if os.path.isdir(directory):
                return True
            else:
                logger.error(f"Путь существует, но не является директорией: {directory}")
                return False
        
        # Создание директории
        os.makedirs(directory, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Ошибка при создании директории {directory}: {e}")
        return False

async def verify_file_integrity(file_path: str, content: str = None) -> bool:
    """Проверка целостности файла"""
    try:
        if not os.path.exists(file_path):
            return False
            
        if content is not None:
            # Проверка по содержимому
            async with aiofiles.open(file_path, 'r', encoding='utf-8-sig') as f:
                file_content = await f.read()
            return file_content.strip() == content.strip()
        else:
            # Проверка на возможность чтения и записи
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8-sig'):
                    pass
                async with aiofiles.open(file_path, 'a', encoding='utf-8-sig'):
                    pass
                return True
            except:
                return False
    except Exception as e:
        logger.error(f"Ошибка при проверке целостности файла {file_path}: {e}")
        return False

async def repair_file(file_path: str, backup_path: str = None) -> bool:
    """Восстановление поврежденного файла"""
    try:
        # Если указан путь к резервной копии
        if backup_path and os.path.exists(backup_path):
            return await restore_from_backup(backup_path, file_path)
            
        # Пытаемся найти последнюю резервную копию
        backup_files = []
        directory = os.path.dirname(file_path)
        base_name = os.path.basename(file_path)
        
        for f in os.listdir(directory):
            if f.startswith(base_name) and f.endswith('.bak'):
                backup_files.append(os.path.join(directory, f))
                
        if backup_files:
            # Сортируем по времени создания (самый новый первый)
            backup_files.sort(key=lambda x: os.path.getctime(x), reverse=True)
            return await restore_from_backup(backup_files[0], file_path)
            
        # Если нет резервных копий, создаем пустой файл
        async with aiofiles.open(file_path, 'w', encoding='utf-8-sig') as f:
            await f.write('')
        return True
    except Exception as e:
        logger.error(f"Ошибка при восстановлении файла {file_path}: {e}")
        return False

def get_file_hash(file_path: str) -> Optional[str]:
    """Получение хеша файла"""
    try:
        if not os.path.exists(file_path):
            return None
            
        hasher = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        logger.error(f"Ошибка при получении хеша файла {file_path}: {e}")
        return None

async def safe_move_file(src: str, dst: str) -> bool:
    """Безопасное перемещение файла с проверкой"""
    try:
        if not os.path.exists(src):
            logger.error(f"Исходный файл не существует: {src}")
            return False
            
        # Создаем директорию назначения, если её нет
        dst_dir = os.path.dirname(dst)
        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)
            
        # Получаем хеш исходного файла
        src_hash = get_file_hash(src)
        if not src_hash:
            return False
            
        # Копируем файл
        shutil.copy2(src, dst)
        
        # Проверяем хеш скопированного файла
        dst_hash = get_file_hash(dst)
        if dst_hash != src_hash:
            logger.error(f"Ошибка при копировании: хеши файлов не совпадают")
            os.remove(dst)
            return False
            
        # Удаляем исходный файл
        os.remove(src)
        return True
    except Exception as e:
        logger.error(f"Ошибка при перемещении файла {src} в {dst}: {e}")
        return False

async def ensure_file_access(file_path: str, mode: str = 'rw') -> bool:
    """Проверка доступа к файлу"""
    try:
        if 'r' in mode:
            # Проверка на чтение
            async with aiofiles.open(file_path, 'r', encoding='utf-8-sig'):
                pass
                
        if 'w' in mode:
            # Проверка на запись
            async with aiofiles.open(file_path, 'a', encoding='utf-8-sig'):
                pass
                
        return True
    except Exception as e:
        logger.error(f"Ошибка доступа к файлу {file_path} (режим {mode}): {e}")
        return False

def get_unique_filename(base_path: str) -> str:
    """Получение уникального имени файла"""
    if not os.path.exists(base_path):
        return base_path
        
    directory = os.path.dirname(base_path)
    filename = os.path.basename(base_path)
    name, ext = os.path.splitext(filename)
    
    counter = 1
    while True:
        new_name = f"{name}_{counter}{ext}"
        new_path = os.path.join(directory, new_name)
        if not os.path.exists(new_path):
            return new_path
        counter += 1

async def detect_file_encoding(file_path: str) -> str:
    """Определение кодировки файла"""
    try:
        # Читаем первые 4096 байт файла для определения кодировки
        with open(file_path, 'rb') as f:
            raw_data = f.read(4096)
            
        if not raw_data:
            return 'utf-8-sig'
            
        # Проверяем наличие BOM-маркеров
        if raw_data.startswith(codecs.BOM_UTF8):
            return 'utf-8-sig'
        elif raw_data.startswith(codecs.BOM_UTF16_LE):
            return 'utf-16-le'
        elif raw_data.startswith(codecs.BOM_UTF16_BE):
            return 'utf-16-be'
            
        # Используем chardet для определения кодировки
        result = chardet.detect(raw_data)
        if result['confidence'] > 0.7:
            return result['encoding']
            
        # Если не удалось определить, возвращаем UTF-8
        return 'utf-8'
    except Exception as e:
        logger.error(f"Ошибка при определении кодировки файла {file_path}: {e}")
        return 'utf-8'

async def normalize_line_endings(content: str, target_os: str = None) -> str:
    """Нормализация переносов строк для целевой ОС"""
    if target_os is None:
        target_os = platform.system().lower()
        
    # Сначала преобразуем все в Unix-style
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    # Затем преобразуем в нужный формат
    if target_os == 'windows':
        return content.replace('\n', '\r\n')
    return content

async def safe_copy_file(src: str, dst: str, preserve_metadata: bool = True) -> bool:
    """Безопасное копирование файла с проверкой целостности"""
    try:
        if not os.path.exists(src):
            logger.error(f"Исходный файл не существует: {src}")
            return False
            
        # Создаем директорию назначения
        dst_dir = os.path.dirname(dst)
        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)
            
        # Получаем хеш исходного файла
        src_hash = get_file_hash(src)
        if not src_hash:
            return False
            
        # Копируем файл с сохранением метаданных
        if preserve_metadata:
            shutil.copy2(src, dst)
        else:
            shutil.copy(src, dst)
            
        # Проверяем хеш скопированного файла
        dst_hash = get_file_hash(dst)
        if dst_hash != src_hash:
            logger.error(f"Ошибка при копировании: хеши файлов не совпадают")
            os.remove(dst)
            return False
            
        return True
    except Exception as e:
        logger.error(f"Ошибка при копировании файла {src} в {dst}: {e}")
        return False

async def read_file_in_chunks(file_path: str, chunk_size: int = 8192) -> AsyncGenerator[str, None]:
    """Асинхронное чтение файла по частям"""
    try:
        encoding = await detect_file_encoding(file_path)
        async with aiofiles.open(file_path, 'r', encoding=encoding) as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    except Exception as e:
        logger.error(f"Ошибка при чтении файла {file_path} по частям: {e}")
        yield ""

def is_path_traversal(path: str) -> bool:
    """Проверка на path traversal атаку"""
    normalized_path = os.path.normpath(path)
    return any(part in {'..', '.'} for part in normalized_path.split(os.sep))

async def safe_delete_file(file_path: str, secure: bool = False) -> bool:
    """Безопасное удаление файла"""
    try:
        if not os.path.exists(file_path):
            return True
            
        if secure:
            # Перезаписываем файл случайными данными перед удалением
            file_size = os.path.getsize(file_path)
            with open(file_path, 'wb') as f:
                f.write(secrets.token_bytes(file_size))
                
        os.remove(file_path)
        return True
    except Exception as e:
        logger.error(f"Ошибка при удалении файла {file_path}: {e}")
        return False

def get_file_info(file_path: str) -> Dict[str, Any]:
    """Получение информации о файле"""
    try:
        stat = os.stat(file_path)
        return {
            'size': stat.st_size,
            'created': datetime.fromtimestamp(stat.st_ctime),
            'modified': datetime.fromtimestamp(stat.st_mtime),
            'accessed': datetime.fromtimestamp(stat.st_atime),
            'is_file': os.path.isfile(file_path),
            'is_dir': os.path.isdir(file_path),
            'extension': os.path.splitext(file_path)[1].lower(),
            'permissions': oct(stat.st_mode)[-3:]
        }
    except Exception as e:
        logger.error(f"Ошибка при получении информации о файле {file_path}: {e}")
        return {}