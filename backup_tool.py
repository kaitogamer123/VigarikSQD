#!/usr/bin/env python3
"""
Скрипт для управления бэкапами из командной строки.
Примеры команд:
    python backup_tool.py status          # Показать все бэкапы
    python backup_tool.py backup chat_check.py   # Забэкапить файл
    python backup_tool.py restore backups/chat_check_20260819_120530.py
    python backup_tool.py cleanup         # Удалить старые бэкапы (оставить 5)
"""

import sys
from backup_manager import (
    create_backup, list_backups, restore_backup,
    cleanup_old_backups, print_backups_info
)


def show_help():
    print("""
╔══════════════════════════════════════════════════════════════════════════╗
║         МЕНЕДЖЕР РЕЗЕРВНОГО КОПИРОВАНИЯ - ViGarik Squad Bot             ║
╚══════════════════════════════════════════════════════════════════════════╝

КОМАНДЫ:

  status                  - Показать все резервные копии
  backup <файл>          - Создать бэкап файла
                          Пример: python backup_tool.py backup utils/chat_check.py
  
  restore <путь>         - Восстановить файл из бэкапа
                          Пример: python backup_tool.py restore backups/chat_check_20260819_120530.py
  
  cleanup [count]        - Удалить старые бэкапы (оставить последние count версий)
                          Пример: python backup_tool.py cleanup 5
  
  help                   - Показать эту справку

ПРИМЕРЫ:

  # Посмотреть все бэкапы
  python backup_tool.py status

  # Создать бэкап файла config.py перед редактированием
  python backup_tool.py backup config.py

  # Восстановить файл из бэкапа
  python backup_tool.py restore backups/config_20260819_153045.py

  # Удалить все старые бэкапы, оставив только последние 5 версий
  python backup_tool.py cleanup 5

АВТОМАТИЧЕСКОЕ ИСПОЛЬЗОВАНИЕ В КОДЕ:

  from backup_manager import create_backup
  
  # Перед редактированием файла
  create_backup("utils/chat_check.py")
  
  # Теперь редактируем файл...
  with open("utils/chat_check.py", "w") as f:
      f.write(new_content)
""")


def main():
    if len(sys.argv) < 2:
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "status":
        print_backups_info()
    
    elif command == "backup":
        if len(sys.argv) < 3:
            print("❌ Укажите файл для бэкапирования")
            print("Пример: python backup_tool.py backup utils/chat_check.py")
            return
        
        file_path = sys.argv[2]
        backup_path = create_backup(file_path)
        if backup_path:
            print(f"✅ Готово! Бэкап: {backup_path}")
    
    elif command == "restore":
        if len(sys.argv) < 3:
            print("❌ Укажите путь до бэкапа для восстановления")
            return
        
        backup_path = sys.argv[2]
        restore_to = sys.argv[3] if len(sys.argv) > 3 else None
        
        if restore_backup(backup_path, restore_to):
            print("✅ Файл успешно восстановлен")
        else:
            print("❌ Ошибка восстановления")
    
    elif command == "cleanup":
        keep_count = 5
        if len(sys.argv) > 2:
            try:
                keep_count = int(sys.argv[2])
            except ValueError:
                print("❌ Укажите число (сколько версий оставить)")
                return
        
        deleted = cleanup_old_backups(keep_count)
        print(f"✅ Удалено {deleted} старых бэкапов")
    
    elif command == "help":
        show_help()
    
    else:
        print(f"❌ Неизвестная команда: {command}")
        print("Введите: python backup_tool.py help")


if __name__ == "__main__":
    main()
