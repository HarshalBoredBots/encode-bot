import asyncio
import os

from bot import config
from bot.logger import get_logger

log = get_logger(__name__)

async def extract_thumbnail(video_path: str, out_path: str, duration: float = None):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    ss = "5"
    if duration and duration > 0:
        ss = str(max(1, int(duration * 0.1)))

    cmd = [
        config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", ss,
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=320:-2",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    return None
