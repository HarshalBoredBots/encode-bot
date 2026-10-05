from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import config
from bot.func.queue_manager import queue_manager
from bot.utils.access import check_access
from bot.utils.format import humanbytes
import database


# ── /queue ────────────────────────────────────────────────────────────────────
@Client.on_message(filters.command("queue"))
async def queue_cmd(client, message):
    user_id = message.from_user.id if message.from_user else None
    is_owner = user_id == config.OWNER_ID

    if is_owner:
        # Owner sees ALL users' jobs
        all_jobs = [j for j in queue_manager.jobs.values()
                    if j.status in ("pending", "running")]

        if not all_jobs:
            await message.reply_text("📭 No active jobs in queue.")
            return

        running = [j for j in all_jobs if j.status == "running"]
        pending = [j for j in all_jobs if j.status == "pending"]

        lines = [f"📋 **Global Queue** ({len(all_jobs)} job(s))\n"]

        if running:
            lines.append("▶️ **Running:**")
            for j in running:
                name = j.file_name or "unknown"
                lines.append(f"  • `{name}` — user `{j.user_id}`")

        if pending:
            lines.append("\n⏳ **Pending:**")
            for i, j in enumerate(pending, 1):
                name = j.file_name or "unknown"
                lines.append(f"  {i}. `{name}` — user `{j.user_id}`")

        # Cancel-all button for owner
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 Cancel ALL Jobs", callback_data="owner_cancel_all")
        ]])
        await message.reply_text("\n".join(lines), reply_markup=buttons)

    else:
        # Regular users see only their own jobs
        allowed, reason = await check_access(message)
        if not allowed:
            if message.chat.type == "private":
                await message.reply_text(reason)
            return

        jobs = queue_manager.get_user_jobs(user_id)
        if not jobs:
            await message.reply_text("📭 You have no active jobs.")
            return

        lines = [f"📋 **Your Queue** ({len(jobs)} job(s))\n"]
        buttons_row = []
        for i, j in enumerate(jobs, 1):
            status_icon = "▶️" if j.status == "running" else f"{i}."
            name = j.file_name or "unknown"
            lines.append(f"{status_icon} `{name}` — **{j.status}**")
            buttons_row.append(
                InlineKeyboardButton(
                    f"🛑 Cancel #{i}", callback_data=f"cancel_job:{j.job_id}"
                )
            )

        # Put up to 2 cancel buttons per row
        keyboard = [buttons_row[i:i+2] for i in range(0, len(buttons_row), 2)]
        await message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ── /cancel (text command) ────────────────────────────────────────────────────
@Client.on_message(filters.command("cancel"))
async def cancel_cmd(client, message):
    user_id = message.from_user.id if message.from_user else None
    is_owner = user_id == config.OWNER_ID

    # Owner can cancel a specific user's jobs: /cancel <user_id>
    if is_owner and len(message.command) >= 2:
        try:
            target_uid = int(message.command[1])
        except ValueError:
            await message.reply_text("Usage: `/cancel <user_id>` or just `/cancel` to cancel your own jobs.")
            return
        target_jobs = queue_manager.get_user_jobs(target_uid)
        if not target_jobs:
            await message.reply_text(f"No active jobs for user `{target_uid}`.")
            return
        count = 0
        for j in target_jobs:
            if queue_manager.cancel_job(j.job_id):
                count += 1
        await message.reply_text(f"🛑 Cancelled {count} job(s) for user `{target_uid}`.")
        return

    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

    jobs = queue_manager.get_user_jobs(user_id)
    if not jobs:
        await message.reply_text("Nothing to cancel.")
        return

    count = 0
    for j in jobs:
        if queue_manager.cancel_job(j.job_id):
            count += 1
    await message.reply_text(f"🛑 Cancelled {count} job(s).")


# ── Inline cancel buttons ─────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^cancel_job:"))
async def cancel_job_cb(client, cq):
    user_id = cq.from_user.id
    job_id = cq.data.split(":", 1)[1]

    job = queue_manager.jobs.get(job_id)
    if not job:
        await cq.answer("Job not found (already done?).", show_alert=True)
        return

    # Only owner or the job owner can cancel
    if user_id != job.user_id and user_id != config.OWNER_ID:
        await cq.answer("That's not your job.", show_alert=True)
        return

    success = queue_manager.cancel_job(job_id)
    if success:
        await cq.answer("🛑 Cancellation requested.")
        try:
            await cq.message.edit_text(
                cq.message.text.markdown + "\n\n`🛑 Cancellation requested…`"
            )
        except Exception:
            pass
    else:
        await cq.answer("Could not cancel (job may have finished).", show_alert=True)


@Client.on_callback_query(filters.regex(r"^owner_cancel_all$"))
async def owner_cancel_all_cb(client, cq):
    if cq.from_user.id != config.OWNER_ID:
        await cq.answer("Not for you.", show_alert=True)
        return

    all_jobs = [j for j in queue_manager.jobs.values()
                if j.status in ("pending", "running")]
    count = 0
    for j in all_jobs:
        if queue_manager.cancel_job(j.job_id):
            count += 1

    await cq.answer(f"🛑 Cancelled {count} job(s).", show_alert=True)
    try:
        await cq.message.edit_text(f"🛑 All jobs cancelled by owner. ({count} job(s))")
    except Exception:
        pass


# ── /status ───────────────────────────────────────────────────────────────────
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
        f"📊 **Bot Statistics**\n"
        f"• Users: {users}\n"
        f"• Total encodes: {encodes}\n"
        f"• Running: {running}\n"
        f"• Queued: {q}"
    )
