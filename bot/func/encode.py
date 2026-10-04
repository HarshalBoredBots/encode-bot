import asyncio
import copy
import os
import re
import shutil
import time

from bot import config
from bot.logger import get_logger
from bot.utils.format import TimeFormatter, humanbytes
from bot.func import download_manager, upload_manager
from bot.func.ffmpeg_utils import probe_video, generate_ffmpeg_cmd
from bot.func.media import extract_thumbnail
import database

log = get_logger(__name__)

_TIME_RE = re.compile(rb"time=(\d+):(\d+):([\d.]+)")

async def _run_ffmpeg(cmd, job, status_msg, duration, settings):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    last_update = 0.0
    stderr_lines = []

    async def read_stderr():
        nonlocal last_update
        while True:
            try:
                line = await proc.stderr.readline()
            except Exception:
                break
            if not line:
                break
            # Always collect stderr for error reporting
            decoded = line.decode(errors="replace").rstrip()
            if decoded:
                stderr_lines.append(decoded)
            if job.cancel_requested:
                try:
                    proc.kill()
                except Exception:
                    pass
                return
            now = time.time()
            if now - last_update < config.UI_UPDATE_INTERVAL:
                continue
            m = _TIME_RE.search(line)
            if not m or duration <= 0:
                continue
            last_update = now
            h, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
            cur = h * 3600 + mm * 60 + ss
            pct = min(cur / duration, 1.0)
            filled = int(pct * 10)
            bar = "█" * filled + "─" * (10 - filled)
            v = settings["video"]
            try:
                await status_msg.edit_text(
                    f"🎬 Encoding {v['resolution'][0]}...\n"
                    f"[{bar}] {int(pct * 100)}%\n"
                    f"{TimeFormatter(int(cur))} / {TimeFormatter(int(duration))}\n"
                    f"CRF {v['crf']} · {v['codec']} · {v['preset']}"
                )
            except Exception:
                pass

    reader = asyncio.create_task(read_stderr())
    try:
        await asyncio.wait_for(proc.wait(), timeout=config.FFMPEG_WALL_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        log.warning("FFmpeg timed out for job %s", job.job_id)
    finally:
        reader.cancel()
        try:
            await reader
        except (asyncio.CancelledError, Exception):
            pass

    if proc.returncode != 0 or stderr_lines:
        log.error("FFmpeg exit code %s for job %s. stderr:\n%s",
                  proc.returncode, job.job_id, "\n".join(stderr_lines[-20:]))

async def run_encode_job(client, job, media_ref, src_chat_id: int, src_message_id: int):
    user_id = job.user_id
    settings = await database.get_user_settings(user_id)
    video = settings["video"]
    resolutions = list(video.get("resolution") or ["720p"])

    try:
        free = shutil.disk_usage(".").free
    except Exception:
        free = config.MIN_FREE_DISK_BYTES
    if free < config.MIN_FREE_DISK_BYTES:
        msg = (
            f"❌ Not enough free disk space to start encoding.\n"
            f"Free: {humanbytes(free)} · Required: {humanbytes(config.MIN_FREE_DISK_BYTES)}"
        )
        log.warning("Disk space check failed for job %s: %s", job.job_id, msg)
        if status:
            try:
                await status.edit_text(msg)
            except Exception:
                pass
        else:
            try:
                await client.send_message(job.chat_id, msg)
            except Exception:
                pass
        return

    # Use absolute path so FFmpeg output and file-existence checks are consistent
    download_dir = os.path.abspath(config.DOWNLOAD_DIR)
    os.makedirs(download_dir, exist_ok=True)

    try:
        status = await client.send_message(job.chat_id, "⬇️ Downloading source file...")
    except Exception:
        status = None

    input_path = os.path.join(download_dir, f"{job.job_id}.src")

    try:
        input_path = await download_manager.download(
            client, media_ref.file_id, input_path, message=status
        )
    except Exception as e:
        log.exception("Download failed: %s", e)
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

    try:
        probe_data = await probe_video(input_path)
    except Exception as e:
        log.exception("Probe failed: %s", e)
        probe_data = {}

    duration = float(probe_data.get("duration") or 0)

    out_paths = []

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
            output_path = os.path.join(download_dir, f"{job.job_id}_{res}{out_ext}")

            cmd = generate_ffmpeg_cmd(input_path, output_path, local_settings, probe_data)
            log.info("FFmpeg cmd (%s): %s", res, " ".join(cmd))

            await _run_ffmpeg(cmd, job, status, duration, local_settings)

            if job.cancel_requested:
                break
            exists = os.path.isfile(output_path)
            size = os.path.getsize(output_path) if exists else 0
            log.info("Output check: %s exists=%s size=%d", output_path, exists, size)
            if not exists or size == 0:
                log.error("Encoding produced no output for %s (exists=%s size=%d)", res, exists, size)
                if status:
                    try:
                        await status.edit_text(f"❌ Encoding failed for {res} — no output file produced.")
                    except Exception:
                        pass
                continue
            if os.path.getsize(output_path) > config.MAX_OUTPUT_SIZE:
                log.warning("Output %s exceeds max size", output_path)

            out_paths.append((res, output_path))
    except Exception as e:
        log.exception("Encoding pipeline failed: %s", e)
        if status:
            try:
                await status.edit_text(f"❌ Encoding error: {e}")
            except Exception:
                pass

    if job.cancel_requested:
        _safe_remove(input_path)
        for _, p in out_paths:
            _safe_remove(p)
        if status:
            try:
                await status.edit_text("🛑 Cancelled.")
            except Exception:
                pass
        return

    for res, out in out_paths:
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

        caption = (
            f"✅ {res} · {video['codec']} · CRF {video['crf']} · "
            f"{settings['audio']['codec']} {settings['audio']['bitrate']}"
        )
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
            log.exception("Upload failed: %s", e)
            try:
                await client.send_message(job.chat_id, f"❌ Upload failed: {e}")
            except Exception:
                pass

    try:
        in_size = os.path.getsize(input_path) if os.path.isfile(input_path) else 0
    except Exception:
        in_size = 0
    out_size = sum(os.path.getsize(p) for _, p in out_paths if os.path.isfile(p))

    _safe_remove(input_path)
    for _, p in out_paths:
        _safe_remove(p)

    if status:
        try:
            await status.edit_text("✅ Encode complete.")
        except Exception:
            pass

    try:
        await database.inc_stats("encodes", 1)
    except Exception:
        pass

    try:
        ratio = ""
        if out_paths:
            if in_size:
                ratio = f" ({100 * out_size / in_size:.1f}% of source)"
        await client.send_message(
            config.LOG_CHANNEL,
            f"✅ Job `{job.job_id}` complete\n"
            f"File: {job.file_name}\n"
            f"Res: {', '.join(r for r, _ in out_paths) or 'none'}{ratio}",
        )
    except Exception:
        pass

def _safe_remove(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
