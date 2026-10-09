import asyncio
import copy
import os
import re
import shutil
import time

from pyrogram import enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import config
from bot.logger import get_logger
from bot.utils.format import TimeFormatter, humanbytes
from bot.func import download_manager, upload_manager
from bot.func.ffmpeg_utils import probe_video, generate_ffmpeg_cmd
from bot.func.media import extract_thumbnail
from bot.func.telegram_retry import tg_call
import database


log = get_logger(__name__)

_TIME_RE = re.compile(rb"time=(\d+):(\d+):([\d.]+)")


def _cancel_markup(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_job:{job_id}")
    ]])


def _encode_bar(pct: float, cur: float, total: float, speed_fps: float,
                res: str, idx: int, total_res: int,
                codec: str, crf, preset: str, job_id: str) -> str:
    """Build the encoding progress message."""
    filled = int(pct * 10)
    bar    = "█" * filled + "─" * (10 - filled)
    done   = f"{idx + 1}/{total_res}"

    # fps speed line (only when available)
    speed_line = f"⚡ {speed_fps:.1f} fps" if speed_fps > 0 else ""

    # time progress
    cur_fmt   = TimeFormatter(int(cur))
    total_fmt = TimeFormatter(int(total)) if total > 0 else "?"

    return (
        f"🎬 <b>Encoding {res}</b>  ({done})\n"
        f"<code>[{bar}]</code> <b>{int(pct * 100)}%</b>\n"
        f"⏱ {cur_fmt} / {total_fmt}"
        + (f"  ·  {speed_line}" if speed_line else "") + "\n"
        f"🎞 {codec}  ·  CRF {crf}  ·  {preset}"
    )


_FPS_RE = re.compile(rb"fps=\s*([\d.]+)")


async def _run_ffmpeg(cmd, job, status_msg, duration, settings):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    last_update = 0.0
    last_log    = 0.0
    LOG_INTERVAL = 60.0
    stderr_lines = []
    v = settings["video"]

    async def read_stderr():
        nonlocal last_update, last_log
        while True:
            try:
                line = await proc.stderr.readline()
            except Exception:
                break
            if not line:
                break

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
            m   = _TIME_RE.search(line)
            if not m or duration <= 0:
                continue

            h, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
            cur = h * 3600 + mm * 60 + ss
            pct = min(cur / duration, 1.0)

            # fps from the same stderr line
            fps_m     = _FPS_RE.search(line)
            speed_fps = float(fps_m.group(1)) if fps_m else 0.0

            # Heroku log heartbeat
            if now - last_log >= LOG_INTERVAL:
                last_log = now
                log.info(
                    "Encoding [%s] job=%s res=%s %.1f%% (%s / %s) "
                    "crf=%s preset=%s fps=%.1f pid=%d",
                    job.file_name or "unknown",
                    job.job_id[:8],
                    v.get("resolution", ["?"])[0],
                    pct * 100,
                    TimeFormatter(int(cur)),
                    TimeFormatter(int(duration)),
                    v.get("crf", "?"),
                    v.get("preset", "?"),
                    speed_fps,
                    proc.pid,
                )

            # Telegram progress update (throttled)
            if now - last_update < config.UI_UPDATE_INTERVAL:
                continue
            last_update = now

            if status_msg is not None:
                resolutions  = list(v.get("resolution") or ["720p"])
                current_res  = resolutions[0]
                res_idx      = getattr(job, "_current_res_idx", 0)

                text = _encode_bar(
                    pct        = pct,
                    cur        = cur,
                    total      = duration,
                    speed_fps  = speed_fps,
                    res        = current_res,
                    idx        = res_idx,
                    total_res  = len(resolutions),
                    codec      = v.get("codec", "libx264"),
                    crf        = v.get("crf", "?"),
                    preset     = v.get("preset", "?"),
                    job_id     = job.job_id,
                )
                try:
                    await status_msg.edit_text(
                        text,
                        parse_mode   = enums.ParseMode.HTML,
                        reply_markup = _cancel_markup(job.job_id),
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
        log.error(
            "FFmpeg exit code %s for job %s. stderr:\n%s",
            proc.returncode, job.job_id,
            "\n".join(stderr_lines[-20:]),
        )


async def _safe_send_message(client, chat_id, text, **kwargs):
    kwargs.setdefault("parse_mode", enums.ParseMode.HTML)
    try:
        return await tg_call(lambda: client.send_message(chat_id, text, **kwargs))
    except Exception as e:
        log.warning("Could not send status message to %s: %s", chat_id, e)
        return None


async def _safe_edit(status, text, parse_mode=enums.ParseMode.HTML, **kwargs):
    if status is None:
        return
    try:
        await status.edit_text(text, parse_mode=parse_mode, **kwargs)
    except Exception:
        pass


async def run_encode_job(client, job, media_ref, src_chat_id: int, src_message_id: int):
    user_id  = job.user_id
    settings = await database.get_user_settings(user_id)
    video    = settings["video"]
    resolutions = list(video.get("resolution") or ["720p"])

    status = None

    # ── Disk space check ────────────────────────────────────────────────────
    try:
        free = shutil.disk_usage(".").free
    except Exception:
        free = config.MIN_FREE_DISK_BYTES

    if free < config.MIN_FREE_DISK_BYTES:
        msg = (
            f"❌ <b>Not enough disk space</b>\n"
            f"Free: {humanbytes(free)}  ·  Required: {humanbytes(config.MIN_FREE_DISK_BYTES)}"
        )
        log.warning("Disk space check failed for job %s", job.job_id)
        await _safe_send_message(client, job.chat_id, msg, parse_mode=enums.ParseMode.HTML)
        return

    download_dir = os.path.abspath(config.DOWNLOAD_DIR)
    os.makedirs(download_dir, exist_ok=True)

    # ── Download ─────────────────────────────────────────────────────────────
    status = await _safe_send_message(
        client,
        job.chat_id,
        "⬇️ <b>Downloading…</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup = _cancel_markup(job.job_id),
    )

    input_path = os.path.join(download_dir, f"{job.job_id}.src")

    try:
        input_path = await download_manager.download(
            client, media_ref.file_id, input_path, message=status,
            ud_type="Downloading",
        )
    except Exception as e:
        log.exception("Download failed: %s", e)
        await _safe_edit(status, f"❌ <b>Download failed</b>\n<code>{e}</code>")
        return

    if job.cancel_requested:
        _safe_remove(input_path)
        await _safe_edit(status, "🛑 Cancelled.", reply_markup=None)
        return

    # ── Probe ────────────────────────────────────────────────────────────────
    try:
        probe_data = await probe_video(input_path)
    except Exception as e:
        log.exception("Probe failed: %s", e)
        probe_data = {}

    duration = float(probe_data.get("duration") or 0)

    out_paths = []

    # ── Encode loop ──────────────────────────────────────────────────────────
    try:
        for idx, res in enumerate(resolutions):
            if job.cancel_requested:
                break

            job._current_res_idx = idx

            await _safe_edit(
                status,
                (
                    f"🎬 <b>Encoding {res}</b>  ({idx + 1}/{len(resolutions)})\n"
                    f"<code>[──────────]</code> <b>0%</b>\n"
                    f"⏱ 0s / {TimeFormatter(int(duration))}\n"
                    f"🎞 {video.get('codec','libx264')}  ·  "
                    f"CRF {video.get('crf','?')}  ·  {video.get('preset','?')}"
                ),
                parse_mode=enums.ParseMode.HTML,
                reply_markup = _cancel_markup(job.job_id),
            )

            local_settings = copy.deepcopy(settings)
            local_settings["video"]["resolution"] = [res]

            out_ext    = "." + str(video.get("output_format", "mkv"))
            output_path = os.path.join(download_dir, f"{job.job_id}_{res}{out_ext}")

            cmd = generate_ffmpeg_cmd(input_path, output_path, local_settings, probe_data)
            log.info("FFmpeg cmd (%s): %s", res, " ".join(cmd))

            await _run_ffmpeg(cmd, job, status, duration, local_settings)

            if job.cancel_requested:
                break

            exists = os.path.isfile(output_path)
            size   = os.path.getsize(output_path) if exists else 0
            log.info("Output check: %s exists=%s size=%d", output_path, exists, size)

            if not exists or size == 0:
                log.error("Encoding produced no output for %s", res)
                await _safe_edit(
                    status,
                    f"❌ <b>Encoding failed for {res}</b> — no output file produced.",
                    reply_markup = _cancel_markup(job.job_id),
                )
                continue

            if size > config.MAX_OUTPUT_SIZE:
                log.warning("Output %s exceeds max size", output_path)

            out_paths.append((res, output_path))

    except Exception as e:
        log.exception("Encoding pipeline failed: %s", e)
        await _safe_edit(status, f"❌ <b>Encoding error:</b> <code>{e}</code>")

    if job.cancel_requested:
        _safe_remove(input_path)
        for _, p in out_paths:
            _safe_remove(p)
        await _safe_edit(status, "🛑 <b>Cancelled.</b>", parse_mode=enums.ParseMode.HTML, reply_markup=None)
        return

    # ── Upload loop ──────────────────────────────────────────────────────────
    for res, out in out_paths:
        if job.cancel_requested:
            break

        out_size = os.path.getsize(out) if os.path.isfile(out) else 0

        await _safe_edit(
            status,
            (
                f"⬆️ <b>Uploading {res}</b>\n"
                f"<code>[──────────]</code> <b>0%</b>\n"
                f"📦 0 B / {humanbytes(out_size)}\n"
                f"⚡ — · ⏱ ETA: —"
            ),
            parse_mode=enums.ParseMode.HTML,
        )

        # thumbnail
        thumb_path = None
        thumb_dir  = os.path.abspath(config.THUMB_DIR)
        os.makedirs(thumb_dir, exist_ok=True)

        thumb_id = settings.get("thumbnail")
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

        caption = (
            f"✅ {res}  ·  {video['codec']}  ·  CRF {video['crf']}  ·  "
            f"{settings['audio']['codec']} {settings['audio']['bitrate']}"
        )

        try:
            await upload_manager.upload(
                client,
                job.chat_id,
                out,
                thumb       = thumb_path,
                caption     = caption,
                as_video    = bool(settings.get("output_as_video", True)),
                message     = status,
                ud_type     = f"Uploading {res}",
            )
        except Exception as e:
            log.exception("Upload failed: %s", e)
            await _safe_send_message(
                client, job.chat_id,
                f"❌ <b>Upload failed ({res}):</b> <code>{e}</code>",
            )
        finally:
            # Always clean up the thumb — even if upload failed
            _safe_remove(thumb_path)

    # ── Cleanup — always runs, cancel or not ─────────────────────────────────
    try:
        in_size = os.path.getsize(input_path) if os.path.isfile(input_path) else 0
    except Exception:
        in_size = 0
    out_total = sum(os.path.getsize(p) for _, p in out_paths if os.path.isfile(p))

    _safe_remove(input_path)
    for _, p in out_paths:
        _safe_remove(p)

    if job.cancel_requested:
        await _safe_edit(status, "🛑 <b>Cancelled.</b>", parse_mode=enums.ParseMode.HTML, reply_markup=None)
        return

    await _safe_edit(status, "✅ <b>Encode complete!</b>", reply_markup=None)

    try:
        await database.inc_stats("encodes", 1)
    except Exception:
        pass

    try:
        ratio = ""
        if out_paths and in_size:
            ratio = f" ({100 * out_total / in_size:.1f}% of source)"
        await tg_call(lambda: client.send_message(
            config.LOG_CHANNEL,
            f"✅ Job `{job.job_id}` complete\n"
            f"File: {job.file_name}\n"
            f"Res: {', '.join(r for r, _ in out_paths) or 'none'}{ratio}",
        ))
    except Exception:
        pass


def _safe_remove(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
