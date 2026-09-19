"""
Сервис для интеграции с официальным API Brawl Stars.
Расширен: полный профиль игрока + история боёв (battlelog).
"""

import logging
from typing import Optional, Dict, Any, List

import aiohttp

import config

logger = logging.getLogger(__name__)

BRAWL_API_TOKEN = config.BRAWL_API_TOKEN
BASE_URL = "https://api.brawlstars.com/v1"


def clean_tag(tag: str) -> str:
    """Очищает тег от лишних символов и форматирует для URL-запроса."""
    clean = (tag or "").strip().upper().replace("#", "")
    return f"%23{clean}"


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {BRAWL_API_TOKEN}",
    }


async def get_player_profile(player_tag: str) -> Optional[Dict[str, Any]]:
    """Запрашивает профиль игрока напрямую из Brawl Stars API."""
    if not player_tag:
        return None

    formatted_tag = clean_tag(player_tag)
    url = f"{BASE_URL}/players/{formatted_tag}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=12)) as response:
                if response.status == 200:
                    data = await response.json()
                    club_info = data.get("club") or {}
                    if not isinstance(club_info, dict):
                        club_info = {}

                    return {
                        "name": data.get("name") or "Игрок",
                        "tag": data.get("tag") or player_tag,
                        "clan_tag": club_info.get("tag"),
                        "clan_name": club_info.get("name"),
                        "trophies": data.get("trophies") or 0,
                        "highest_trophies": data.get("highestTrophies") or data.get("trophies") or 0,
                        "exp_level": data.get("expLevel") or 0,
                        "solo_wins": data.get("soloVictories") or 0,
                        "duo_wins": data.get("duoVictories") or 0,
                        "trio_wins": data.get("3vs3Victories") or 0,
                        "ranked_rank": data.get("rankedSeasonRank"),
                        "brawlers_count": len(data.get("brawlers") or []),
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


async def get_player_battlelog(player_tag: str) -> List[Dict[str, Any]]:
    """Возвращает последние бои игрока из официального battlelog API."""
    if not player_tag:
        return []

    formatted_tag = clean_tag(player_tag)
    url = f"{BASE_URL}/players/{formatted_tag}/battlelog"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("items") or []
                    return items if isinstance(items, list) else []
                elif response.status == 404:
                    logger.warning(f"Battlelog не найден для тега {player_tag} (404).")
                    return []
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка battlelog API: {response.status}. Ответ: {error_text}")
                    return []
        except Exception as e:
            logger.error(f"Сетевая ошибка при запросе battlelog: {e}")
            return []
