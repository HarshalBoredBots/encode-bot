from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import config
from bot.func.queue_manager import queue_manager
from bot.utils.access import check_access
from bot.utils.format import humanbytes
import database


def _short_id(job_id: str) -> str:
    """Return the first 8 chars of the UUID as a short display ID."""
    return job_id[:8]


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
                sid  = _short_id(j.job_id)
                lines.append(f"  • `{name}`\n    🆔 `{sid}` — user `{j.user_id}`")

        if pending:
            lines.append("\n⏳ **Pending:**")
            for i, j in enumerate(pending, 1):
                name = j.file_name or "unknown"
                sid  = _short_id(j.job_id)
                lines.append(f"  {i}. `{name}`\n    🆔 `{sid}` — user `{j.user_id}`")

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
            sid  = _short_id(j.job_id)
            lines.append(f"{status_icon} `{name}` — **{j.status}**\n    🆔 `{sid}`")
            buttons_row.append(
                InlineKeyboardButton(
                    f"🛑 Cancel #{i} ({sid})", callback_data=f"cancel_job:{j.job_id}"
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

    # Owner can cancel by user_id or by short job ID: /cancel <user_id|job_id>
    if is_owner and len(message.command) >= 2:
        arg = message.command[1]

        # Try to match a short job ID first (8 hex chars)
        matched_job = None
        for j in queue_manager.jobs.values():
            if j.job_id.startswith(arg) and j.status in ("pending", "running"):
                matched_job = j
                break

        if matched_job:
            success = queue_manager.cancel_job(matched_job.job_id)
            if success:
                await message.reply_text(
                    f"🛑 Cancelled job `{_short_id(matched_job.job_id)}` "
                    f"(`{matched_job.file_name or 'unknown'}`)."
                )
            else:
                await message.reply_text("Could not cancel (job may have just finished).")
            return

        # Fall back to treating arg as a user_id
        try:
            target_uid = int(arg)
        except ValueError:
            await message.reply_text(
                "Usage:\n"
                "`/cancel` — cancel your own jobs\n"
                "`/cancel <user_id>` — cancel all jobs for that user\n"
                "`/cancel <job_id>` — cancel a specific job by its short ID"
            )
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
    job_id  = cq.data.split(":", 1)[1]

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
                "🛑 <b>Cancellation requested…</b>",
                parse_mode="html",
                reply_markup=None,
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

    stats   = await database.get_stats()
    encodes = int(stats.get("encodes", 0))
    users   = len(await database.full_userbase())
    q       = queue_manager.queue.qsize()
    running = sum(1 for j in queue_manager.jobs.values() if j.status == "running")

    # Show running jobs with their short IDs
    running_jobs = [j for j in queue_manager.jobs.values() if j.status == "running"]
    running_lines = ""
    if running_jobs:
        running_lines = "\n" + "\n".join(
            f"  ▶️ `{_short_id(j.job_id)}` — `{j.file_name or 'unknown'}`"
            for j in running_jobs
        )

    await message.reply_text(
        f"📊 **Bot Statistics**\n"
        f"• Users: {users}\n"
        f"• Total encodes: {encodes}\n"
        f"• Running: {running}{running_lines}\n"
        f"• Queued: {q}"
    )
