"""Telegram movie-code bot with JSON storage and Admin panel (Group support)."""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# HTTP request logs can contain Telegram API URLs, so keep them out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CATALOG_FILE = "catalog.json"


def load_catalog() -> dict[str, object]:
    """Load the catalog, keeping older string entries compatible."""
    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as catalog_file:
            catalog = json.load(catalog_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not load catalog file")
        raise

    if not isinstance(catalog, dict):
        raise ValueError("Catalog must be a JSON object")
    return catalog


def save_catalog(catalog: dict[str, object]) -> None:
    """Atomically save the catalog to disk."""
    temp_file = f"{CATALOG_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as catalog_file:
            json.dump(catalog, catalog_file, ensure_ascii=False, indent=2)
        os.replace(temp_file, CATALOG_FILE)
    except OSError:
        logger.exception("Could not save catalog file")
        try:
            os.remove(temp_file)
        except OSError:
            pass
        raise


def is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome users and explain how to find a movie."""
    message = update.effective_message
    if message is not None:
        await message.reply_text("Salom! Kino kodi yoki nomini yuboring:")


async def admin_panel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the admin menu to the configured admin."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if user.id != ADMIN_ID:
        await message.reply_text("Sizda bu buyruqdan foydalanish huquqi yo‘q!")
        return

    keyboard = [
        [InlineKeyboardButton("Statistika", callback_data="admin_stats")],
        [
            InlineKeyboardButton(
                "Kino qo‘shish",
                callback_data="admin_add_movie",
            )
        ],
    ]
    await message.reply_text(
        "<b>Xush kelibsiz, admin!</b>\nKerakli amalni tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def admin_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle admin menu button selections."""
    query = update.callback_query
    if query is None:
        return

    if query.from_user.id != ADMIN_ID:
        await query.answer("Siz admin emassiz!", show_alert=True)
        return

    await query.answer()
    if query.data == "admin_stats":
        try:
            catalog = load_catalog()
        except (OSError, ValueError):
            await query.edit_message_text("Katalogni o‘qib bo‘lmadi.")
            return
        video_count = sum(
            isinstance(movie, dict) and movie.get("type") == "video"
            for movie in catalog.values()
        )
        link_count = len(catalog) - video_count
        await query.edit_message_text(
            f"Bot statistikasi:\n"
            f"Jami kino: {len(catalog)}\n"
            f"Video: {video_count}\n"
            f"Havola: {link_count}"
        )
    elif query.data == "admin_add_movie":
        context.user_data["awaiting_movie_add"] = True
        context.user_data.pop("pending_movie_code", None)
        await query.edit_message_text(
            "Kino qo‘shish uchun:\n"
            "• Videoni captioniga kino kodini yozib yuboring; yoki\n"
            "• Kod va havolani yuboring: 101 https://example.com/video\n\n"
            "Bekor qilish: /cancel"
        )


async def delete_movie(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Delete a movie code from the catalog; Telegram media itself remains."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if user.id != ADMIN_ID:
        await message.reply_text("Bu buyruq faqat admin uchun.")
        return

    if not context.args or not context.args[0].strip():
        await message.reply_text("Foydalanish: /del 101")
        return

    code = context.args[0].strip().upper()
    try:
        catalog = load_catalog()
    except (OSError, ValueError):
        logger.exception("Could not read catalog while deleting movie code")
        await message.reply_text("Kino katalogini o‘qib bo‘lmadi.")
        return

    if code not in catalog:
        await message.reply_text(f"{code} kodi katalogda topilmadi.")
        return

    del catalog[code]
    try:
        save_catalog(catalog)
    except OSError:
        logger.exception("Could not save catalog after deleting movie code")
        await message.reply_text("Kino kodini katalogdan o‘chirib bo‘lmadi.")
        return

    await message.reply_text(
        f"{code} kodi katalogdan o‘chirildi. Telegramdagi asl video o‘chmaydi."
    )


async def add_movie(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Add a movie using /add CODE URL or a replied-to/uploaded video."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if user.id != ADMIN_ID:
        await message.reply_text("Bu buyruq faqat admin uchun.")
        return

    args = context.args
    if not args:
        context.user_data["awaiting_movie_add"] = True
        context.user_data.pop("pending_movie_code", None)
        await message.reply_text(
            "Video yuboring (captionida kod bo‘lsin) yoki "
            "`kod https://havola` yozing. Bekor qilish: /cancel"
        )
        return

    code = args[0].strip().upper()
    video = message.video
    if video is None and message.reply_to_message is not None:
        video = message.reply_to_message.video

    if video is not None:
        await store_movie(
            message,
            code,
            {"type": "video", "file_id": video.file_id},
        )
        return

    if len(args) >= 2 and is_http_url(args[1]):
        await store_movie(message, code, {"type": "link", "url": args[1]})
        return

    if len(args) == 1:
        context.user_data["awaiting_movie_add"] = True
        context.user_data["pending_movie_code"] = code
        await message.reply_text(
            f"{code} kodi tanlandi. Shu kodga video yuboring yoki "
            "video havolasini jo‘nating. Bekor qilish: /cancel"
        )
        return

    await message.reply_text(
        "Foydalanish: /add 101 https://example.com/video "
        "yoki videoga javob qilib /add 101 yuboring."
    )


async def store_movie(
    message,
    code: str,
    movie: dict[str, str],
) -> bool:
    """Save one movie entry and report success or failure to the admin."""
    try:
        catalog = load_catalog()
    except (OSError, ValueError):
        await message.reply_text("Kino katalogini o‘qib bo‘lmadi.")
        return False

    catalog[code] = movie
    try:
        save_catalog(catalog)
    except OSError:
        await message.reply_text("Kinoni katalogga saqlab bo‘lmadi.")
        return False

    await message.reply_text(f"{code} kodi uchun kino saqlandi.")
    return True


async def cancel_add(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Cancel an in-progress movie addition."""
    context.user_data.pop("awaiting_movie_add", None)
    context.user_data.pop("pending_movie_code", None)
    message = update.effective_message
    if message is not None:
        await message.reply_text("Kino qo‘shish bekor qilindi.")


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Accept pending admin additions or look up a movie code."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if context.user_data.get("awaiting_movie_add") and user.id == ADMIN_ID:
        code_from_command = context.user_data.get("pending_movie_code")
        if message.video is not None:
            code = message.caption.strip().upper() if message.caption else ""
            code = code or (code_from_command or "")
            if not code:
                await message.reply_text(
                    "Videoning captioniga kino kodini yozing yoki avval /add <kod> yuboring."
                )
                return
            saved = await store_movie(
                message,
                code,
                {"type": "video", "file_id": message.video.file_id},
            )
            if saved:
                context.user_data.pop("awaiting_movie_add", None)
                context.user_data.pop("pending_movie_code", None)
            return

        if message.text is None:
            return

        parts = message.text.strip().split(maxsplit=1)
        if code_from_command:
            code = code_from_command
            url = parts[0] if parts else ""
        elif len(parts) == 2:
            code, url = parts[0].upper(), parts[1]
        else:
            await message.reply_text(
                "Kod va havolani birga yuboring, masalan: "
                "101 https://example.com/video"
            )
            return

        if not is_http_url(url):
            await message.reply_text("Havola http:// yoki https:// bilan boshlanishi kerak.")
            return
        saved = await store_movie(message, code, {"type": "link", "url": url})
        if saved:
            context.user_data.pop("awaiting_movie_add", None)
            context.user_data.pop("pending_movie_code", None)
        return

    if message.text is None:
        return

    code = message.text.strip().upper()
    try:
        catalog = load_catalog()
    except (OSError, ValueError):
        logger.exception("Catalog file contains invalid JSON")
        await message.reply_text("Kino katalogida xatolik bor. Admin bilan bog‘laning.")
        return

    movie = catalog.get(code)
    if movie is None:
        await message.reply_text("Kechirasiz, bunday kodli kino topilmadi.")
    elif isinstance(movie, dict) and movie.get("type") == "video":
        file_id = movie.get("file_id")
        if file_id:
            await message.reply_video(video=file_id, supports_streaming=True)
        else:
            await message.reply_text("Bu kino videosi katalogda noto‘g‘ri saqlangan.")
    elif isinstance(movie, dict) and movie.get("type") == "link":
        url = movie.get("url")
        if url:
            await message.reply_text(url, disable_web_page_preview=False)
        else:
            await message.reply_text("Bu kino havolasi katalogda noto‘g‘ri saqlangan.")
    else:
        # Older catalog entries stored the link as a plain string.
        await message.reply_text(str(movie), disable_web_page_preview=False)


def main() -> None:
    """Configure and run the Telegram bot."""
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Xatolik: Replit Secrets'da BOT_TOKEN topilmadi!")
    if ADMIN_ID == 0:
        logger.warning("ADMIN_ID sozlanmagan; admin panelidan foydalanib bo‘lmaydi.")

    application = ApplicationBuilder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("add", add_movie))
    application.add_handler(CommandHandler("del", delete_movie))
    application.add_handler(CommandHandler("cancel", cancel_add))
    application.add_handler(CallbackQueryHandler(admin_callback))
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.VIDEO) & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info("Bot ishga tushdi.")
    application.run_polling()


if __name__ == "__main__":
    main()