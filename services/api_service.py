"""
Сервис для интеграции с официальным API Brawl Stars
"""

import logging
import aiohttp
from typing import Optional, Dict, Any

# Импортируем ваш существующий файл конфигурации config.py
import config

logger = logging.getLogger(__name__)

# Подтягиваем реальный токен из вашего конфига
BRAWL_API_TOKEN = config.BRAWL_API_TOKEN
BASE_URL = "https://api.brawlstars.com/v1"


def clean_tag(tag: str) -> str:
    """Очищает тег от лишних символов и форматирует для URL-запроса."""
    clean = tag.strip().upper().replace("#", "")
    return f"%23{clean}"


async def get_player_profile(player_tag: str) -> Optional[Dict[str, Any]]:
    """Запрашивает профиль игрока напрямую из Brawl Stars API с поддержкой Лиги."""
    formatted_tag = clean_tag(player_tag)
    url = f"{BASE_URL}/players/{formatted_tag}"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {BRAWL_API_TOKEN}"
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    club_info = data.get("club", {})

                    return {
                        "name": data.get("name"),            # Игровой ник
                        "clan_tag": club_info.get("tag"),    # Тег текущего клуба
                        "trophies": data.get("trophies"),    # Его кубки
                        "ranked_rank": data.get("rankedSeasonRank", 1) # Текущий ранг Лиги (1-19)
                    }
                elif response.status == 404:
                    logger.warning(f"Игрок с тегом {player_tag} не найден в API Brawl Stars (404).")
                    return None
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка Brawl Stars API: {response.status}. Ответ: {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Сетевая ошибка при запросе к Brawl Stars API: {e}")
            return None
