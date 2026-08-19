"""
Менеджер резервного копирования файлов.
Автоматически сохраняет старые версии файлов перед редактированием.
Бэкапы не попадают в git (исключены в .gitignore).
"""

import os
import shutil
from datetime import datetime
from pathlib import Path


BACKUP_DIR = "backups"


def ensure_backup_dir():
    """Создает папку для бэкапов если её нет."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        print(f"✅ Создана папка бэкапов: {BACKUP_DIR}/")


def create_backup(file_path: str) -> str:
    """
    Создает резервную копию файла перед редактированием.
    
    Args:
        file_path: путь до файла который нужно забэкапить
    
    Returns:
        Путь до созданного бэкапа
    
    Example:
        backup_file = create_backup("utils/chat_check.py")
        print(f"Бэкап создан: {backup_file}")
    """
    ensure_backup_dir()
    
    if not os.path.exists(file_path):
        print(f"❌ Файл не найден: {file_path}")
        return None
    
    # Читаем оригинальный файл
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Генерируем имя бэкапа: filename_YYYYMMDD_HHMMSS.ext
    base_name = Path(file_path).stem
    extension = Path(file_path).suffix
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    backup_name = f"{base_name}_{timestamp}{extension}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    
    # Сохраняем бэкап
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"💾 Бэкап создан: {backup_path}")
    return backup_path


def list_backups(file_name_filter: str = None) -> list:
    """
    Выводит список всех бэкапов.
    
    Args:
        file_name_filter: фильтр по имени файла (опционально)
    
    Returns:
        Список файлов бэкапов
    """
    ensure_backup_dir()
    
    backups = []
    for file in sorted(os.listdir(BACKUP_DIR)):
        if file_name_filter and file_name_filter not in file:
            continue
        
        file_path = os.path.join(BACKUP_DIR, file)
        if os.path.isfile(file_path):
            size = os.path.getsize(file_path)
            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            backups.append({
                'name': file,
                'path': file_path,
                'size': size,
                'modified': mtime
            })
    
    return backups


def restore_backup(backup_path: str, restore_path: str = None) -> bool:
    """
    Восстанавливает файл из бэкапа.
    
    Args:
        backup_path: путь до файла бэкапа
        restore_path: куда восстановить (если None, восстановит на место оригинала)
    
    Returns:
        True если успешно, False если ошибка
    
    Example:
        restore_backup("backups/chat_check_20260819_120530.py", "utils/chat_check.py")
    """
    if not os.path.exists(backup_path):
        print(f"❌ Бэкап не найден: {backup_path}")
        return False
    
    # Определяем путь восстановления
    if restore_path is None:
        # Пытаемся угадать оригинальный путь из имени бэкапа
        file_name = Path(backup_path).stem.rsplit('_', 2)[0]  # Убираем timestamp
        extension = Path(backup_path).suffix
        restore_path = f"{file_name}{extension}"
    
    # Создаем backup перед восстановлением (на случай если файл важный)
    if os.path.exists(restore_path):
        create_backup(restore_path)
    
    try:
        shutil.copy2(backup_path, restore_path)
        print(f"✅ Файл восстановлен: {restore_path}")
        return True
    except Exception as e:
        print(f"❌ Ошибка восстановления: {e}")
        return False


def cleanup_old_backups(keep_count: int = 5) -> int:
    """
    Удаляет старые бэкапы, оставляя только последние N версий каждого файла.
    
    Args:
        keep_count: сколько последних версий оставить для каждого файла
    
    Returns:
        Количество удаленных файлов
    """
    ensure_backup_dir()
    
    # Группируем бэкапы по оригинальному файлу
    file_groups = {}
    
    for file in os.listdir(BACKUP_DIR):
        file_path = os.path.join(BACKUP_DIR, file)
        if not os.path.isfile(file_path):
            continue
        
        # Извлекаем оригинальное имя файла (до первого timestamp)
        base_name = file.rsplit('_', 2)[0]
        
        if base_name not in file_groups:
            file_groups[base_name] = []
        
        file_groups[base_name].append({
            'name': file,
            'path': file_path,
            'mtime': os.path.getmtime(file_path)
        })
    
    deleted_count = 0
    
    # Удаляем старые файлы, оставляя только последние keep_count
    for group_files in file_groups.values():
        sorted_files = sorted(group_files, key=lambda x: x['mtime'], reverse=True)
        
        for old_file in sorted_files[keep_count:]:
            try:
                os.remove(old_file['path'])
                print(f"🗑️  Удален старый бэкап: {old_file['name']}")
                deleted_count += 1
            except Exception as e:
                print(f"⚠️  Не удалось удалить {old_file['name']}: {e}")
    
    if deleted_count == 0:
        print("✅ Нет старых бэкапов для удаления")
    
    return deleted_count


def print_backups_info():
    """Выводит информацию о всех бэкапах."""
    backups = list_backups()
    
    if not backups:
        print("📭 Нет бэкапов")
        return
    
    print("\n📁 РЕЗЕРВНЫЕ КОПИИ ФАЙЛОВ:\n")
    print(f"{'Файл':<40} {'Размер':<10} {'Дата':<20}")
    print("─" * 70)
    
    for backup in backups:
        size_kb = backup['size'] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
        date_str = backup['modified'].strftime("%Y-%m-%d %H:%M:%S")
        print(f"{backup['name']:<40} {size_str:<10} {date_str:<20}")
    
    print("─" * 70)
    print(f"Всего бэкапов: {len(backups)}")


# ─── ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ ──────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔧 Менеджер резервного копирования файлов ViGarik Squad Bot\n")
    
    # Примеры команд
    print("ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ:")
    print("1. Создать бэкап:")
    print('   backup_manager.create_backup("utils/chat_check.py")\n')
    
    print("2. Посмотреть все бэкапы:")
    print('   backups = backup_manager.list_backups()\n')
    
    print("3. Очистить старые бэкапы:")
    print('   backup_manager.cleanup_old_backups(keep_count=5)\n')
    
    print("4. Восстановить из бэкапа:")
    print('   backup_manager.restore_backup("backups/chat_check_20260819_120530.py")\n')
    
    print("─" * 70)
    print_backups_info()
