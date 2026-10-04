import asyncio
import subprocess
import sys
import os
import urllib.request
import tarfile

from pyrogram import idle

from bot import app, config
from bot.config import validate_config
from bot.logger import get_logger
from bot.server import start_server
from bot.utils.listener import register_listener
from bot.func.queue_manager import queue_manager
import database

log = get_logger(__name__)

async def check_ffmpeg():
    def _has_drawtext(bin_path):
        try:
            out = subprocess.check_output(
                [bin_path, "-hide_banner", "-filters"],
                stderr=subprocess.STDOUT,
            ).decode(errors="ignore")
            return "drawtext" in out
        except Exception:
            return False

    if not _has_drawtext(config.FFMPEG_BIN):
        static_dir = "/app/ffmpeg-static"
        static_ffmpeg = os.path.join(static_dir, "ffmpeg")
        static_ffprobe = os.path.join(static_dir, "ffprobe")

        if not os.path.isfile(static_ffmpeg) or not _has_drawtext(static_ffmpeg):
            os.makedirs(static_dir, exist_ok=True)
            url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
            tmp = "/tmp/ffmpeg-static.tar.xz"
            log.info("Downloading static ffmpeg with full filter support...")
            urllib.request.urlretrieve(url, tmp)
            log.info("Extracting static ffmpeg...")
            with tarfile.open(tmp, "r:xz") as tar:
                for member in tar.getmembers():
                    basename = os.path.basename(member.name)
                    if basename in ("ffmpeg", "ffprobe") and member.isfile():
                        dest = os.path.join(static_dir, basename)
                        with tar.extractfile(member) as src, open(dest, "wb") as dst:
                            dst.write(src.read())
                        os.chmod(dest, 0o755)
                        log.info("Extracted %s -> %s", member.name, dest)
            os.remove(tmp)

        if not os.path.isfile(static_ffmpeg):
            raise RuntimeError("Failed to extract static ffmpeg binary")

        config.FFMPEG_BIN = static_ffmpeg
        config.FFPROBE_BIN = static_ffprobe
        log.info("Switched to static ffmpeg: %s", static_ffmpeg)

    try:
        out = subprocess.check_output(
            [config.FFMPEG_BIN, "-hide_banner", "-filters"],
            stderr=subprocess.STDOUT,
        ).decode(errors="ignore")
        for f in ("drawtext", "scale", "overlay"):
            if f not in out:
                raise RuntimeError(f"ffmpeg is missing required filter: {f}")

        enc = subprocess.check_output(
            [config.FFMPEG_BIN, "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
        ).decode(errors="ignore")
        for c in ("libx264", "libx265"):
            if c not in enc:
                log.warning("ffmpeg missing optional encoder: %s", c)

        subprocess.check_output(
            [config.FFPROBE_BIN, "-version"], stderr=subprocess.STDOUT
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"ffmpeg/ffprobe not installed: {e}")

async def main():
    validate_config()
    await check_ffmpeg()
    await database.init_db()
    await start_server(config.PORT)
    register_listener(app)
    async with app:
        await queue_manager.start_worker()
        log.info("Bot started as %s v%s", config.BOT_NAME, config.BOT_VERSION)
        try:
            await idle()
        finally:
            await queue_manager.stop()
            log.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
