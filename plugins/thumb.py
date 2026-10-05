"""
plugins/thumb.py

Commands
--------
/setthumbnail   – reply to a photo OR send a photo with this caption to save it
/delthumbnail   – remove saved thumbnail (bot will auto-extract from video)
/getthumbnail   – show your current saved thumbnail
"""

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message

from bot.utils.access import check_access
from bot.logger import get_logger
import database

log = get_logger(__name__)


@Client.on_message(filters.command("setthumbnail"))
async def cmd_setthumbnail(client: Client, message: Message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_text(reason)
        return

    user_id = message.from_user.id

    # Case 1: /setthumbnail sent as caption on a photo
    photo = None
    if message.photo:
        photo = message.photo

    # Case 2: reply to a photo
    if not photo and message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo

    if not photo:
        return await message.reply_text(
            "↩️ **How to set thumbnail:**\n\n"
            "• Send a photo with caption `/setthumbnail`\n"
            "• Or reply to any photo with `/setthumbnail`"
        )

    # Save file_id — no disk download needed, Telegram hosts it
    settings = await database.get_user_settings(user_id)
    settings["thumbnail"] = photo.file_id
    await database.update_user_settings(user_id, settings)
    log.info("Thumbnail saved for user %s (file_id=%s)", user_id, photo.file_id)
    await message.reply_text("✅ Thumbnail saved! It will be used for all future encodes.")


@Client.on_message(filters.command("delthumbnail"))
async def cmd_delthumbnail(client: Client, message: Message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_text(reason)
        return

    user_id = message.from_user.id
    settings = await database.get_user_settings(user_id)
    settings["thumbnail"] = None
    await database.update_user_settings(user_id, settings)
    await message.reply_text(
        "🗑 Thumbnail removed.\n"
        "The bot will now auto-extract a frame from each encoded video."
    )


@Client.on_message(filters.command("getthumbnail"))
async def cmd_getthumbnail(client: Client, message: Message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_text(reason)
        return

    user_id = message.from_user.id
    settings = await database.get_user_settings(user_id)
    thumb = settings.get("thumbnail")

    if not thumb:
        return await message.reply_text(
            "No custom thumbnail set.\n"
            "Bot will auto-extract a frame from each video.\n\n"
            "Use `/setthumbnail` to set one."
        )

    try:
        await client.send_photo(
            message.chat.id,
            photo=thumb,
            caption="🖼 Your current thumbnail.\n\nUse `/delthumbnail` to remove it.",
            reply_to_message_id=message.id,
        )
    except Exception as e:
        log.exception("Failed to send thumbnail preview: %s", e)
        # file_id may be stale — clear it
        settings["thumbnail"] = None
        await database.update_user_settings(user_id, settings)
        await message.reply_text(
            "⚠️ Saved thumbnail could not be retrieved (it may have expired).\n"
            "It has been cleared. Please set a new one with `/setthumbnail`."
        )
