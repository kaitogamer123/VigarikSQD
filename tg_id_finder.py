import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

# Вставь сюда токен своего бота от @BotFather
BOT_TOKEN = "8970676348:AAHms48E-Zqj9W5Ostm48lu7Tt-V2fZPO0M"

dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)


@dp.message(Command("start"))
async def cmd_start(message: Message):
  await message.answer(
      "👋 Привет! Отправь мне список юзернеймов (каждый с новой строки или через"
      " пробел), например:\n"
      "@dvernayaru4ka\n"
      "Milkivway\n"
      "@benorizx\n\n"
      "И я выдам тебе их Telegram ID!"
  )


@dp.message(F.text)
async def process_tags(message: Message):
  # Достаем все слова/строки из сообщения пользователя
  text = message.text.strip()
  # Разбиваем по строкам или пробелам
  lines = text.splitlines()

  raw_usernames = []
  for line in lines:
    # Если в строке несколько через пробел
    parts = line.split()
    for p in parts:
      clean_username = p.strip().lstrip("@")
      if clean_username:
        raw_usernames.append(clean_username)

  if not raw_usernames:
    await message.answer("❌ Я не нашел ни одного юзернейма в твоем сообщении.")
    return

  await message.answer(
      f"⏳ Начинаю поиск ID для {len(raw_usernames)} пользователей..."
  )

  results = []

  for uname in raw_usernames:
    try:
      # Запрашиваем информацию о пользователе через Telegram API
      chat = await bot.get_chat(f"@{uname}")
      results.append(
          f"ID: `{chat.id}` — @{uname}"
          + (f" ({chat.first_name})" if chat.first_name else "")
      )
    except Exception as e:
      results.append(f"❌ Не найден / Ошибка для `@{uname}`: {e}")
    # Небольшая задержка, чтобы не словить лимиты Telegram API (FloodWait)
    await asyncio.sleep(0.3)

  # Отправляем результат (разбиваем, если слишком длинный, но для небольших списков ок)
  response_text = "\n".join(results)
  # Телеграм имеет лимит в 4096 символов на сообщение, если что
  if len(response_text) > 4000:
    for x in range(0, len(response_text), 4000):
      await message.answer(response_text[x : x + 4000], parse_mode="Markdown")
  else:
    await message.answer(response_text, parse_mode="Markdown")


async def main():
  print("🤖 Бот-помощник для сбора ID запущен и ждет сообщения...")
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())