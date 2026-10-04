from pyrogram import Client, filters

from bot.func.queue_manager import queue_manager
from bot.utils.access import check_access
import database


@Client.on_message(filters.command("queue"))
async def queue_cmd(client, message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

    jobs = queue_manager.get_user_jobs(message.from_user.id)
    if not jobs:
        await message.reply_text("No active jobs.")
        return
    lines = ["**Your jobs**"]
    for i, j in enumerate(jobs, 1):
        lines.append(f"{i}. `{j.file_name}` — **{j.status}**")
    await message.reply_text("\n".join(lines))


@Client.on_message(filters.command("cancel"))
async def cancel_cmd(client, message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

    jobs = queue_manager.get_user_jobs(message.from_user.id)
    if not jobs:
        await message.reply_text("Nothing to cancel.")
        return
    for j in jobs:
        queue_manager.cancel_job(j.job_id)
    await message.reply_text(f"🛑 Cancelled {len(jobs)} job(s).")


@Client.on_message(filters.command("status"))
async def status_cmd(client, message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

    stats = await database.get_stats()
    encodes = int(stats.get("encodes", 0))
    users = len(await database.full_userbase())
    q = queue_manager.queue.qsize()
    running = sum(1 for j in queue_manager.jobs.values() if j.status == "running")
    await message.reply_text(
        f"📊 **Bot statistics**\n"
        f"• Users: {users}\n"
        f"• Total encodes: {encodes}\n"
        f"• Running: {running}\n"
        f"• Queued: {q}"
    )
