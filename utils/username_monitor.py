""" Фоновая задача для отслеживания смены юзернеймов у участников ViGarik Squad. Защищена от сбоев HTML-разметки и спама. """
import logging
import aiosqlite  # Используем aiosqlite для работы с той же БД
from aiogram import Bot
from aiogram.utils.markdown import html_decoration as hd
from database import get_all_members, DB_PATH
from config import INITIAL_ADMINS

logger = logging.getLogger(__name__)

async def update_just_username(user_id: int, new_username: str | None):
    """Точечно и безопасно обновляет юзернейм в базе данных."""
    # Очищаем юзернейм от символа '@', если он есть
    clean_username = new_username.lstrip("@") if new_username else None

    try:
        async with aiosqlite.connect(DB_PATH, timeout=20.0) as db:
            await db.execute(
                "UPDATE members SET username = ?, updated_at = datetime('now') WHERE user_id = ?",
                (clean_username, user_id)
            )
            await db.commit()
            logger.info(f"✅ Успешно обновлен юзернейм в БД для ID {user_id}: {clean_username}")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления юзернейма в БД для ID {user_id}: {e}")
async def check_and_update_usernames(bot: Bot):
    """Фоновая задача: проверяет смену юзернеймов у игроков с известным ID."""
    logger.info("Запуск плановой проверки юзернеймов...")
    all_members = await get_all_members()

    # Собираем ID президентов из конфига и базы данных
    presidents = set(uid for uid, info in INITIAL_ADMINS.items() if info.get("role") == "president")
    for m in all_members:
        if m.get("role") == "president" and m.get("user_id"):
            presidents.add(int(m["user_id"]))

    for m in all_members:
        uid = m.get("user_id")
        old_username = m.get("username")

        # Проверяем только тех, у кого реальный ID уже есть в базе
        if not uid or int(uid) <= 0:
            continue

        try:
            # Запрашиваем актуальный профиль из Telegram API
            chat = await bot.get_chat(chat_id=int(uid))
            new_username = chat.username

            # 1. Нормализуем СТАРЫЙ юзернейм из базы
            clean_old = old_username.lstrip("@").strip() if old_username else None
            if clean_old and clean_old.lower() in ["none", "unknown", "нет", "null", ""]:
                clean_old = None

            # 2. Нормализуем НОВЫЙ юзернейм из Telegram API
            clean_new = new_username.lstrip("@").strip() if new_username else None
            if clean_new and clean_new.lower() in ["none", "unknown", "нет", "null", ""]:
                clean_new = None

            # 3. Сравниваем регистронезависимо (.lower())
            old_compare = clean_old.lower() if clean_old else None
            new_compare = clean_new.lower() if clean_new else None

            if old_compare != new_compare:
                # Обновляем БД (сохраняем оригинальный регистр clean_new)
                await update_just_username(int(uid), clean_new)

                # Форматируем красивый вывод для сообщения
                old_display = f"@{hd.quote(str(clean_old))}" if clean_old else "отсутствовал"
                new_display = f"<b>@{hd.quote(str(clean_new))}</b>" if clean_new else "<b>удален ❌</b>"

                raw_nick = m.get("game_nick") or "Без ника"
                game_nick = hd.quote(str(raw_nick))

                msg_text = (
                    "🔔 <b>Уведомление о смене юзернейма!</b>\n\n" 
                    f"Игрок: <b>{game_nick}</b> (ID: <code>{uid}</code>)\n" 
                    f"Старый тег: {old_display}\n" 
                    f"Новый тег: {new_display}"
                )

                # Рассылаем лидерам
                for pres_id in presidents:
                    try:
                        await bot.send_message(chat_id=pres_id, text=msg_text, parse_mode="HTML")
                    except Exception:
                        pass

                logger.info(f"Юзернейм игрока {uid} успешно изменен и сохранен: {clean_old} -> {clean_new}")

        except Exception as e:
            # Игрок мог заблокировать бота или сменить настройки приватности, пропускаем
            logger.debug(f"Не удалось проверить юзернейм для ID {uid}: {e}")