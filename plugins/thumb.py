"""
plugins/thumb.py
Quick /setthumbnail command — reply to any photo with /setthumbnail to save it.
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.utils.access import check_access
import database


@Client.on_message(filters.command("setthumbnail"))
async def cmd_setthumbnail(client: Client, message: Message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply_text(
            "↩️ Reply to a photo with /setthumbnail to save it as your thumbnail."
        )

    photo = message.reply_to_message.photo
    settings = await database.get_user_settings(message.from_user.id)
    settings["thumbnail"] = photo.file_id
    await database.update_user_settings(message.from_user.id, settings)
    await message.reply_text("✅ Thumbnail saved.")
