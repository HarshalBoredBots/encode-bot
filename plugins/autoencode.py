"""
plugins/autoencode.py
═══════════════════════════════════════════════════════════════════════════════
Auto-encode feature for encode-bot-main.

Commands
────────
/autoencode [template]  – set or view the auto-encode filename template
/stopautoencode         – clear the template and stop auto mode

Media handler (group=1)
───────────────────────
When a user has an active template, every video/document sent in private is
automatically queued for encoding WITHOUT the manual confirm-button flow.
The existing confirm-button handler in plugins/encode.py (group=0) is skipped
for these users via an early-return guard added to that file.

Template placeholders
─────────────────────
  {episode}  – zero-padded episode number (e.g. 05)
  {season}   – season number (e.g. 1)
  {quality}  – quality indicator extracted from source filename (e.g. 1080p)
  {audio}    – audio label extracted from source filename (e.g. Dual, Hindi)
═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import copy
import os
import re
import shutil
import time
import uuid

from pyrogram import Client, filters
from pyrogram.types import Message

from bot import config
from bot.func.encode import _run_ffmpeg, _safe_remove
from bot.func.ffmpeg_utils import probe_video, generate_ffmpeg_cmd
from bot.func.media import extract_thumbnail
from bot.func import download_manager, upload_manager
from bot.func.queue_manager import queue_manager, Job
from bot.utils.format import humanbytes, TimeFormatter, resolve_encode_template
from bot.logger import get_logger
import database

log = get_logger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_video_doc(message: Message) -> bool:
    if message.video:
        return True
    if message.document:
        return (message.document.mime_type or "").lower().startswith("video/")
    return False


def _source_filename(message: Message) -> str:
    """Return the best available filename from the media object."""
    media = message.video or message.document
    if media and getattr(media, "file_name", None):
        return media.file_name
    return "video.mkv"


# ── /autoencode command ───────────────────────────────────────────────────────

@Client.on_message(filters.private & filters.command("autoencode"))
async def cmd_autoencode(client: Client, message: Message):
    user_id = message.from_user.id

    if await database.is_user_banned(user_id):
        await message.reply_text("🚫 You are banned.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        # No argument — show current template
        current = await database.get_autoencode_template(user_id)
        if current:
            await message.reply_text(
                f"⚙️ **Auto-encode is ON**\n\n"
                f"**Current template:**\n`{current}`\n\n"
                f"**Supported placeholders:** "
                f"`{{episode}}` `{{season}}` `{{quality}}` `{{audio}}`\n\n"
                f"Send `/autoencode <template>` to change it, or "
                f"`/stopautoencode` to disable."
            )
        else:
            await message.reply_text(
                "⚙️ **Auto-encode is OFF**\n\n"
                "No template is set. Send video files one by one after setting a template.\n\n"
                "**Usage:**\n"
                "`/autoencode [S1-E{episode}] Show Title [{quality}] [Dual] @Channel.mkv`\n\n"
                "**Supported placeholders:** "
                "`{episode}` `{season}` `{quality}` `{audio}`"
            )
        return

    template = parts[1].strip()
    await database.set_autoencode_template(user_id, template)
    await message.reply_text(
        f"✅ **Auto-encode template saved!**\n\n"
        f"`{template}`\n\n"
        f"Now just send video files — each one will be encoded automatically "
        f"using your `/settings` and uploaded with the resolved filename.\n\n"
        f"**Placeholders:** `{{episode}}` `{{season}}` `{{quality}}` `{{audio}}`\n"
        f"Send `/stopautoencode` to disable."
    )


# ── /stopautoencode command ───────────────────────────────────────────────────

@Client.on_message(filters.private & filters.command("stopautoencode"))
async def cmd_stopautoencode(client: Client, message: Message):
    user_id = message.from_user.id
    await database.set_autoencode_template(user_id, "")
    await message.reply_text(
        "✅ Auto encode stopped. Files will no longer be auto-queued."
    )


# ── Media handler (group=1, fires AFTER the confirm-button handler in group=0) ─

@Client.on_message(
    filters.private & (filters.video | filters.document),
    group=1,
)
async def on_video_autoencode(client: Client, message: Message):
    if not _is_video_doc(message):
        return

    user_id = message.from_user.id

    # Check if the user has an active template
    template = await database.get_autoencode_template(user_id)
    if not template:
        # No template → the group=0 handler in encode.py already handled this
        return

    # ── Validations ───────────────────────────────────────────────────────────
    if await database.is_user_banned(user_id):
        await message.reply_text("🚫 You are banned.")
        return

    media = message.video or message.document
    file_size = media.file_size or 0
    if file_size > config.MAX_FILE_SIZE:
        await message.reply_text(
            f"❌ File too large: {humanbytes(file_size)} "
            f"(limit {humanbytes(config.MAX_FILE_SIZE)})."
        )
        return

    if len(queue_manager.get_user_jobs(user_id)) >= config.MAX_JOBS_PER_USER:
        await message.reply_text(
            "⚠️ You already have an active job. Use /queue or /cancel first."
        )
        return

    # ── Resolve output filename ───────────────────────────────────────────────
    source_fn = _source_filename(message)
    output_filename = resolve_encode_template(template, source_fn)

    # ── Build and queue the job ───────────────────────────────────────────────
    job = Job(
        job_id=str(uuid.uuid4()),
        user_id=user_id,
        func=run_autoencode_job,
        file_name=source_fn,
        file_size=humanbytes(file_size),
        chat_id=message.chat.id,
        message_id=message.id,
    )
    job.args = (client, job)
    job.kwargs = {
        "media_ref": media,
        "src_chat_id": message.chat.id,
        "src_message_id": message.id,
        "output_filename": output_filename,
    }

    ok, info = await queue_manager.add_job(job)
    if not ok:
        await message.reply_text(f"⚠️ {info}")
        return

    await message.reply_text(
        f"⚙️ Auto-encode queued!\n\n"
        f"**Output:** `{output_filename}`\n"
        f"Queue position: {info}"
    )


# ── run_autoencode_job ────────────────────────────────────────────────────────

async def run_autoencode_job(
    client,
    job: Job,
    media_ref,
    src_chat_id: int,
    src_message_id: int,
    output_filename: str,
):
    """
    Encode pipeline for auto-encode jobs.

    Identical to run_encode_job in bot/func/encode.py except:
      • Uses *output_filename* (resolved from the user's template) as the
        upload caption and document filename instead of a generic label.
      • No "Start Encode" button — runs fully automatically.
    """
    user_id  = job.user_id
    settings = await database.get_user_settings(user_id)
    video    = settings["video"]
    resolutions = list(video.get("resolution") or ["720p"])

    # ── Disk space check ──────────────────────────────────────────────────────
    try:
        free = shutil.disk_usage(".").free
    except Exception:
        free = config.MIN_FREE_DISK_BYTES
    if free < config.MIN_FREE_DISK_BYTES:
        msg = (
            f"❌ Not enough free disk space to start encoding.\n"
            f"Free: {humanbytes(free)} · Required: {humanbytes(config.MIN_FREE_DISK_BYTES)}"
        )
        log.warning("Disk space check failed for auto job %s: %s", job.job_id, msg)
        try:
            await client.send_message(job.chat_id, msg)
        except Exception:
            pass
        return

    download_dir = os.path.abspath(config.DOWNLOAD_DIR)
    os.makedirs(download_dir, exist_ok=True)

    try:
        status = await client.send_message(job.chat_id, "⬇️ Downloading source file...")
    except Exception:
        status = None

    input_path = os.path.join(download_dir, f"{job.job_id}.src")

    # ── Download ──────────────────────────────────────────────────────────────
    try:
        input_path = await download_manager.download(
            client, media_ref.file_id, input_path, message=status
        )
    except Exception as e:
        log.exception("Auto-encode download failed: %s", e)
        if status:
            try:
                await status.edit_text(f"❌ Download failed: {e}")
            except Exception:
                pass
        return

    if job.cancel_requested:
        _safe_remove(input_path)
        if status:
            try:
                await status.edit_text("🛑 Cancelled.")
            except Exception:
                pass
        return

    # ── Probe ─────────────────────────────────────────────────────────────────
    try:
        probe_data = await probe_video(input_path)
    except Exception as e:
        log.exception("Probe failed: %s", e)
        probe_data = {}

    duration = float(probe_data.get("duration") or 0)

    out_paths = []

    # ── Encode loop (one pass per resolution) ────────────────────────────────
    try:
        for idx, res in enumerate(resolutions):
            if job.cancel_requested:
                break
            if status:
                try:
                    await status.edit_text(
                        f"🎬 Encoding {res} ({idx + 1}/{len(resolutions)})..."
                    )
                except Exception:
                    pass

            local_settings = copy.deepcopy(settings)
            local_settings["video"]["resolution"] = [res]

            out_ext = "." + str(video.get("output_format", "mkv"))

            # For a single resolution use the resolved output filename directly;
            # for multiple resolutions append the resolution tag before the ext.
            if len(resolutions) == 1:
                out_name   = output_filename
                out_base, out_ext_orig = os.path.splitext(output_filename)
                output_path = os.path.join(
                    download_dir, f"{job.job_id}_{res}{out_ext_orig or out_ext}"
                )
            else:
                out_base, out_ext_orig = os.path.splitext(output_filename)
                out_name    = f"{out_base} [{res}]{out_ext_orig or out_ext}"
                output_path = os.path.join(
                    download_dir, f"{job.job_id}_{res}{out_ext_orig or out_ext}"
                )

            cmd = generate_ffmpeg_cmd(input_path, output_path, local_settings, probe_data)
            log.info("Auto FFmpeg cmd (%s): %s", res, " ".join(cmd))

            await _run_ffmpeg(cmd, job, status, duration, local_settings)

            if job.cancel_requested:
                break
            exists = os.path.isfile(output_path)
            size   = os.path.getsize(output_path) if exists else 0
            if not exists or size == 0:
                log.error(
                    "Auto-encode produced no output for %s (exists=%s size=%d)",
                    res, exists, size,
                )
                if status:
                    try:
                        await status.edit_text(
                            f"❌ Encoding failed for {res} — no output file produced."
                        )
                    except Exception:
                        pass
                continue

            out_paths.append((res, output_path, out_name))

    except Exception as e:
        log.exception("Auto-encode pipeline failed: %s", e)
        if status:
            try:
                await status.edit_text(f"❌ Encoding error: {e}")
            except Exception:
                pass

    if job.cancel_requested:
        _safe_remove(input_path)
        for _, p, _ in out_paths:
            _safe_remove(p)
        if status:
            try:
                await status.edit_text("🛑 Cancelled.")
            except Exception:
                pass
        return

    # ── Upload ────────────────────────────────────────────────────────────────
    for res, out, out_name in out_paths:
        if job.cancel_requested:
            break

        thumb_path = settings.get("thumbnail")
        if not thumb_path:
            thumb_dir = os.path.abspath(config.THUMB_DIR)
            os.makedirs(thumb_dir, exist_ok=True)
            thumb_path = os.path.join(thumb_dir, f"{job.job_id}_{res}.jpg")
            try:
                if not await extract_thumbnail(out, thumb_path, duration):
                    thumb_path = None
            except Exception:
                thumb_path = None

        # Caption uses the resolved output filename
        caption = out_name

        try:
            await upload_manager.upload(
                client,
                job.chat_id,
                out,
                thumb=thumb_path,
                caption=caption,
                as_video=bool(settings.get("output_as_video", True)),
                message=status,
            )
        except Exception as e:
            log.exception("Auto-encode upload failed: %s", e)
            try:
                await client.send_message(job.chat_id, f"❌ Upload failed: {e}")
            except Exception:
                pass

    # ── Cleanup ───────────────────────────────────────────────────────────────
    _safe_remove(input_path)
    for _, p, _ in out_paths:
        _safe_remove(p)

    if status:
        try:
            await status.edit_text("✅ Auto-encode complete.")
        except Exception:
            pass

    try:
        await database.inc_stats("encodes", 1)
    except Exception:
        pass

    try:
        in_size  = 0
        out_size = sum(
            os.path.getsize(p) for _, p, _ in out_paths if os.path.isfile(p)
        )
        ratio = f" ({100 * out_size / in_size:.1f}% of source)" if in_size else ""
        await client.send_message(
            config.LOG_CHANNEL,
            f"✅ Auto job `{job.job_id}` complete\n"
            f"Output: {output_filename}\n"
            f"Res: {', '.join(r for r, _, _ in out_paths) or 'none'}{ratio}",
        )
    except Exception:
        pass
