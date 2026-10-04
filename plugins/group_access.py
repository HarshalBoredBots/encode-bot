"""
plugins/group_access.py
Owner-only commands to manage group and premium access.

Commands
--------
/addgroup [group_id]        – approve a group
/removegroup [group_id]     – revoke a group's access
/listgroups                 – list all approved groups
/addpremium <user_id>       – grant premium (DM) access
/removepremium <user_id>    – revoke premium access
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from bot import config
import database


@Client.on_message(filters.command("addgroup"))
async def cmd_addgroup(client: Client, message: Message):
    if message.from_user.id != config.OWNER_ID:
        return

    if message.chat.type in ("group", "supergroup") and len(message.command) == 1:
        group_id = message.chat.id
    elif len(message.command) == 2:
        try:
            group_id = int(message.command[1])
        except ValueError:
            return await message.reply_text("❌ Invalid group ID.")
    else:
        return await message.reply_text("Usage: /addgroup [group_id]")

    await database.add_allowed_group(group_id, message.from_user.id)
    await message.reply_text(f"✅ Group `{group_id}` approved.")


@Client.on_message(filters.command("removegroup"))
async def cmd_removegroup(client: Client, message: Message):
    if message.from_user.id != config.OWNER_ID:
        return

    if message.chat.type in ("group", "supergroup") and len(message.command) == 1:
        group_id = message.chat.id
    elif len(message.command) == 2:
        try:
            group_id = int(message.command[1])
        except ValueError:
            return await message.reply_text("❌ Invalid group ID.")
    else:
        return await message.reply_text("Usage: /removegroup [group_id]")

    await database.remove_allowed_group(group_id)
    await message.reply_text(f"✅ Group `{group_id}` removed.")


@Client.on_message(filters.command("listgroups"))
async def cmd_listgroups(client: Client, message: Message):
    if message.from_user.id != config.OWNER_ID:
        return

    groups = await database.list_allowed_groups()
    if not groups:
        return await message.reply_text("No approved groups.")

    lines = ["**Approved groups:**"]
    for gid in groups:
        lines.append(f"• `{gid}`")
    await message.reply_text("\n".join(lines))


@Client.on_message(filters.command("addpremium"))
async def cmd_addpremium(client: Client, message: Message):
    if message.from_user.id != config.OWNER_ID:
        return

    if len(message.command) != 2:
        return await message.reply_text("Usage: /addpremium <user_id>")
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    await database.set_premium_user(user_id, True)
    await message.reply_text(f"✅ User `{user_id}` granted premium access.")


@Client.on_message(filters.command("removepremium"))
async def cmd_removepremium(client: Client, message: Message):
    if message.from_user.id != config.OWNER_ID:
        return

    if len(message.command) != 2:
        return await message.reply_text("Usage: /removepremium <user_id>")
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    await database.set_premium_user(user_id, False)
    await message.reply_text(f"✅ User `{user_id}` premium access revoked.")
