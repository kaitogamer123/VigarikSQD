"""Совместимый запуск старого db_manager.py с актуальным config.py."""
import runpy

import config


# Старый db_manager.py импортирует BS_API_TOKEN, а актуальный config.py хранит
# этот же токен под именем BRAWL_API_TOKEN. Добавляем алиас только в памяти.
if not hasattr(config, "BS_API_TOKEN"):
    config.BS_API_TOKEN = getattr(config, "BRAWL_API_TOKEN", None)

if not config.BS_API_TOKEN:
    raise RuntimeError("В config.py не задан BRAWL_API_TOKEN")


if __name__ == "__main__":
    runpy.run_path("db_manager.py", run_name="__main__")