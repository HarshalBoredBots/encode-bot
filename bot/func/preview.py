import asyncio
import os

from bot import config
from bot.logger import get_logger

log = get_logger(__name__)

async def generate_preview(input_path: str, out_path: str, seconds: int = 10):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cmd = [
        config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-i", input_path,
        "-t", str(seconds),
        "-c", "copy",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return out_path if os.path.isfile(out_path) else None
