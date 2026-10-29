"""Telegram movie-code bot with JSON storage and Admin panel (Group support)."""

from __future__ import annotations

import json
import logging
import os
from typing import Final

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CATALOG_FILE = "catalog.json"

def load_catalog() -> dict[str, dict[str, str]]:
    """Load movie catalog from JSON file."""
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Could not load catalog file")
    return {}

def save_catalog(catalog: dict[str, dict[str, str]]) -> None:
    """Save movie catalog to JSON file."""
    try:
        with open(CATALOG_FILE, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=4)
    except Exception:
        logger.exception("Could not save catalog file")

# Initialize catalog
MOVIE_CATALOG: dict[str, dict[str, str]] = load_catalog()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome users and explain how to look up a movie."""
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Kino kodini yuboring va men sizga uning videosi yoki havolasini yuboraman. "
            "Yo‘riqnoma uchun /help buyrug‘idan foydalaning."
        )


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Explain the supported movie code formats."""
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Kino kodini yozma ravishda yuboring. Kodlar katta-kichik harflarga "
            "farq qilmagan holda qidiriladi."
        )


async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to add a movie via private chat or group."""
    message = update.effective_message
    user = update.effective_user
    
    if message is None or user is None:
        return

    # Check if user is admin
    admin_id = os.getenv("ADMIN_ID")
    if not admin_id or str(user.id) != str(admin_id):
        await message.reply_text("Kechirasiz, bu buyruq faqat admin uchun!")
        return

    args = context.args
    if not args:
        await message.reply_text(
            "Xato format! Foydalanish:\n"
            "1) Videoga izoh (caption) qilib yuborish: `/add 101`\n"
            "2) Videoga javob (reply) qilib yuborish: `/add 101`\n"
            "3) Havola uchun: `/add 101 https://t.me/...`",
            parse_mode="Markdown"
        )
        return

    code = args[0].strip().upper()

    # 1. Agar video bilan birga yuborilgan bo'lsa (caption orqali)
    if message.video:
        file_id = message.video.file_id
        MOVIE_CATALOG[code] = {"type": "video", "file_id": file_id}
        save_catalog(MOVIE_CATALOG)
        await message.reply_text(f"Muvaffaqiyatli! `{code}` kodi uchun video saqlandi.", parse_mode="Markdown")
        return

    # 2. Agar mavjud videoga reply (javob) qilingan bo'lsa
    if message.reply_to_message and message.reply_to_message.video:
        file_id = message.reply_to_message.video.file_id
        MOVIE_CATALOG[code] = {"type": "video", "file_id": file_id}
        save_catalog(MOVIE_CATALOG)
        await message.reply_text(f"Muvaffaqiyatli! `{code}` kodi uchun video saqlandi.", parse_mode="Markdown")
        return

    # 3. Agar havola berilgan bo'lsa
    if len(args) < 2:
        await message.reply_text("Iltimos, havola ham kiriting yoki videoga qo'shib yuboring!")
        return

    url = args[1].strip()
    MOVIE_CATALOG[code] = {"type": "link", "url": url}
    save_catalog(MOVIE_CATALOG)
    await message.reply_text(f"Muvaffaqiyatli! `{code}` kodi uchun havola saqlandi.", parse_mode="Markdown")


async def delete_movie(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Remove a movie code from the catalog. This does not delete Telegram media."""
    message = update.effective_message
    user = update.effective_user

    if message is None or user is None:
        return

    admin_id = os.getenv("ADMIN_ID")
    if not admin_id or str(user.id) != str(admin_id):
        await message.reply_text("Kechirasiz, bu buyruq faqat admin uchun!")
        return

    args = context.args
    if not args:
        await message.reply_text("Foydalanish: /del 101")
        return

    code = args[0].strip().upper()
    if code not in MOVIE_CATALOG:
        await message.reply_text(f"{code} kodi katalogda topilmadi.")
        return

    del MOVIE_CATALOG[code]
    save_catalog(MOVIE_CATALOG)
    await message.reply_text(
        f"{code} kodi katalogdan o‘chirildi. Telegramdagi asl video o‘chmaydi."
    )


async def handle_movie_code(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Look up a code and send its configured video or link."""
    message = update.effective_message
    if message is None or message.text is None:
        return

    code = message.text.strip().upper()
    movie = MOVIE_CATALOG.get(code)

    if movie is None:
        # Avoid replying to ordinary group chat, but explain missing numeric codes.
        chat_type = message.chat.type if message.chat is not None else None
        if chat_type == "private" or code.isdigit():
            await message.reply_text(
                "Bu kino kodi katalogda topilmadi. Admin uni "
                "/add <kod> <havola> buyrug‘i bilan qo‘shishi mumkin."
            )
        return

    try:
        movie_type = movie.get("type")
        if movie_type == "link":
            url = movie.get("url")
            if not url:
                return
            await message.reply_text(url, disable_web_page_preview=False)
        elif movie_type == "video":
            file_id = movie.get("file_id")
            if not file_id:
                return
            await message.reply_video(video=file_id, supports_streaming=True)
    except TelegramError:
        logger.exception("Telegram could not deliver a movie for code %s", code)


def main() -> None:
    """Configure and start the bot."""
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "BOT_TOKEN topilmadi. Replit Secrets'ga BOT_TOKEN nomli kalit qo‘shing."
        )

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_movie))
    application.add_handler(CommandHandler("del", delete_movie))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_movie_code)
    )

    logger.info("Movie code bot is starting.")
    application.run_polling()


if __name__ == "__main__":
    main()
    