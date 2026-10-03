import asyncio
import subprocess
import sys

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
