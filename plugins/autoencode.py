"""
plugins/autoencode.py
Auto-encode feature: set a template, then every video sent is auto-queued.

Commands
--------
/autoencode [template]  – set or view the auto-encode filename template
/stopautoencode         – clear the template and stop auto mode
"""

import asyncio
import copy
import os
import re
import shutil
import time
import uuid

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from bot import config
from bot.func.encode import _run_ffmpeg, _safe_remove
from bot.func.ffmpeg_utils import probe_video, generate_ffmpeg_cmd
from bot.func.media import extract_thumbnail
from bot.func import download_manager, upload_manager
from bot.func.queue_manager import queue_manager, Job
from bot.func.telegram_retry import tg_call
from bot.utils.format import humanbytes, TimeFormatter, resolve_encode_template
from bot.utils.access import check_access
from bot.logger import get_logger
import database


async def _safe_send(client, chat_id, text, **kwargs):
    """Send a message with DC5 retry; returns message or None on failure."""
    # Always use HTML parse mode unless caller explicitly overrides
    kwargs.setdefault("parse_mode", enums.ParseMode.HTML)
    try:
        return await tg_call(lambda: client.send_message(chat_id, text, **kwargs))
    except Exception as e:
        log.warning("Could not send message to %s: %s", chat_id, e)
        return None


async def _safe_edit(status, text, **kwargs):
    """Edit status message silently; no-op if status is None."""
    if status is None:
        return
    # Always use HTML parse mode unless caller explicitly overrides
    kwargs.setdefault("parse_mode", enums.ParseMode.HTML)
    try:
        await status.edit_text(text, **kwargs)
    except Exception:
        pass

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

@Client.on_message(filters.command("autoencode"))
async def cmd_autoencode(client: Client, message: Message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

    user_id = message.from_user.id

    if await database.is_user_banned(user_id):
        await message.reply_text("🚫 You are banned.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
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
                "No template is set.\n\n"
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

@Client.on_message(filters.command("stopautoencode"))
async def cmd_stopautoencode(client: Client, message: Message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

    user_id = message.from_user.id
    await database.set_autoencode_template(user_id, "")
    await message.reply_text(
        "✅ Auto encode stopped. Files will no longer be auto-queued."
    )


# ── Media handler (group=1, fires AFTER the confirm-button handler in group=0) ─

@Client.on_message(
    (filters.video | filters.document),
    group=1,
)
async def on_video_autoencode(client: Client, message: Message):
    if not _is_video_doc(message):
        return

    user_id = message.from_user.id

    # Check if the user has an active template
    template = await database.get_autoencode_template(user_id)
    if not template:
        return

    # Access guard
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return

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

    # ── Resolve output filename from encode settings ──────────────────────────
    settings = await database.get_user_settings(user_id)
    encode_res = settings["video"]["resolution"][0]
    source_fn = _source_filename(message)
    output_filename = resolve_encode_template(template, source_fn, encode_res)

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

    pos = info
    if queue_manager.user_running_count(user_id) > 0 or pos > 1:
        await message.reply_text(
            f"📥 Added to queue. Position: {pos}\n\n"
            f"**Output:** `{output_filename}`"
        )
    else:
        await message.reply_text(
            f"⚙️ Auto-encode queued!\n\n"
            f"**Output:** `{output_filename}`\n"
            f"Queue position: {pos}"
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
        await _safe_send(client, job.chat_id, msg)
        return

    download_dir = os.path.abspath(config.DOWNLOAD_DIR)
    os.makedirs(download_dir, exist_ok=True)

    status = await _safe_send(
        client, job.chat_id,
        "⬇️ <b>Downloading…</b>",
    )

    input_path = os.path.join(download_dir, f"{job.job_id}.src")

    # ── Download ──────────────────────────────────────────────────────────────
    try:
        input_path = await download_manager.download(
            client, media_ref.file_id, input_path,
            message=status, ud_type="Downloading",
        )
    except Exception as e:
        log.exception("Auto-encode download failed: %s", e)
        await _safe_edit(status, f"❌ Download failed: {e}")
        return

    if job.cancel_requested:
        _safe_remove(input_path)
        await _safe_edit(status, "🛑 Cancelled.")
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
            await _safe_edit(status, f"🎬 Encoding {res} ({idx + 1}/{len(resolutions)})...")

            local_settings = copy.deepcopy(settings)
            local_settings["video"]["resolution"] = [res]

            out_ext = "." + str(video.get("output_format", "mkv"))

            if len(resolutions) == 1:
                out_name = output_filename
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
                await _safe_edit(status, f"❌ Encoding failed for {res} — no output file produced.")
                continue

            out_paths.append((res, output_path, out_name))

    except Exception as e:
        log.exception("Auto-encode pipeline failed: %s", e)
        await _safe_edit(status, f"❌ Encoding error: {e}")

    if job.cancel_requested:
        _safe_remove(input_path)
        for _, p, _ in out_paths:
            _safe_remove(p)
        await _safe_edit(status, "🛑 Cancelled.")
        return

    # ── Upload ────────────────────────────────────────────────────────────────
    for res, out, out_name in out_paths:
        if job.cancel_requested:
            break

        thumb_path = None
        thumb_dir = os.path.abspath(config.THUMB_DIR)
        os.makedirs(thumb_dir, exist_ok=True)

        thumb_id = settings.get("thumbnail")  # Telegram file_id or None
        if thumb_id:
            thumb_path = os.path.join(thumb_dir, f"{job.job_id}_{res}_custom.jpg")
            try:
                await client.download_media(thumb_id, file_name=thumb_path)
                if not os.path.isfile(thumb_path) or os.path.getsize(thumb_path) == 0:
                    _safe_remove(thumb_path)
                    thumb_path = None
            except Exception:
                _safe_remove(thumb_path)
                thumb_path = None
        else:
            thumb_path = os.path.join(thumb_dir, f"{job.job_id}_{res}.jpg")
            try:
                if not await extract_thumbnail(out, thumb_path, duration):
                    _safe_remove(thumb_path)
                    thumb_path = None
            except Exception:
                _safe_remove(thumb_path)
                thumb_path = None

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
                file_name=out_name,
                ud_type=f"Uploading {res}",
            )
        except Exception as e:
            log.exception("Auto-encode upload failed: %s", e)
            await _safe_send(client, job.chat_id, f"❌ Upload failed: {e}")
        finally:
            # Always clean up the thumb — even if upload failed
            _safe_remove(thumb_path)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    _safe_remove(input_path)
    for _, p, _ in out_paths:
        _safe_remove(p)

    await _safe_edit(status, "✅ Auto-encode complete.")

    try:
        await database.inc_stats("encodes", 1)
    except Exception:
        pass

    try:
        await tg_call(lambda: client.send_message(
            config.LOG_CHANNEL,
            f"✅ Auto job `{job.job_id}` complete\n"
            f"Output: {output_filename}\n"
            f"Res: {', '.join(r for r, _, _ in out_paths) or 'none'}",
        ))
    except Exception:
        pass
