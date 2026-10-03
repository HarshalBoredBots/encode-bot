import os

from pyrogram import Client, filters

from bot import config
from bot.decorator import is_admin
from bot.logger import get_logger, LOG_FILE
from bot.utils.shell import run_shell
from bot.utils.restart import restart_bot
import database

log = get_logger(__name__)

@Client.on_message(filters.command("broadcast") & filters.private)
@is_admin
async def broadcast_cmd(client, message):
    if len(message.command) < 2 and not message.reply_to_message:
        await message.reply_text("Usage: /broadcast <text> or reply to a message.")
        return

    if message.reply_to_message:
        payload = message.reply_to_message
    else:
        payload = " ".join(message.command[1:])

    users = await database.full_userbase()
    sent = failed = 0
    status = await message.reply_text(f"Broadcasting to {len(users)} users...")
    for uid in users:
        try:
            if isinstance(payload, str):
                await client.send_message(uid, payload)
            else:
                await payload.copy(uid)
            sent += 1
        except Exception:
            failed += 1
    try:
        await status.edit_text(f"✅ Done. Sent: {sent}, Failed: {failed}.")
    except Exception:
        pass

@Client.on_message(filters.command("ban") & filters.private)
@is_admin
async def ban_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /ban <user_id>")
        return
    try:
        uid = int(message.command[1])
    except ValueError:
        await message.reply_text("Invalid user id.")
        return
    await database.ban_user(uid)
    await message.reply_text(f"🚫 Banned {uid}.")

@Client.on_message(filters.command("unban") & filters.private)
@is_admin
async def unban_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /unban <user_id>")
        return
    try:
        uid = int(message.command[1])
    except ValueError:
        await message.reply_text("Invalid user id.")
        return
    await database.unban_user(uid)
    await message.reply_text(f"✅ Unbanned {uid}.")

@Client.on_message(filters.command("stats") & filters.private)
@is_admin
async def stats_cmd(client, message):
    stats = await database.get_stats()
    users = await database.full_userbase()
    await message.reply_text(
        f"📈 **Statistics**\n"
        f"• Users: {len(users)}\n"
        f"• Encodes: {stats.get('encodes', 0)}"
    )

@Client.on_message(filters.command("log") & filters.private)
@is_admin
async def log_cmd(client, message):
    if os.path.isfile(LOG_FILE):
        await message.reply_document(LOG_FILE, caption="Latest log file.")
    else:
        await message.reply_text("No log file found.")

@Client.on_message(filters.command("shell") & filters.private)
async def shell_cmd(client, message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.reply_text("Dukhi Atma! 😔")
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /shell <command>")
        return
    cmd = " ".join(message.command[1:])
    out = await run_shell(cmd)
    if len(out) > 3800:
        out = out[:3800] + "\n...truncated"
    await message.reply_text(f"```\n{out}\n```")

@Client.on_message(filters.command("restart") & filters.private)
async def restart_cmd(client, message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.reply_text("Dukhi Atma! 😔")
        return
    await message.reply_text("♻️ Restarting...")
    await restart_bot()
