import asyncio
import subprocess
import sys
import os
import urllib.request
import tarfile
import zipfile

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

    def _download_btbn():
        """Download BtbN static ffmpeg build which includes libfreetype/drawtext."""
        static_dir = "/app/ffmpeg-static"
        os.makedirs(static_dir, exist_ok=True)
        # BtbN linux64 static build - full GPL build with all filters
        url = (
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
            "ffmpeg-master-latest-linux64-gpl.tar.xz"
        )
        tmp = "/tmp/ffmpeg-btbn.tar.xz"
        log.info("Downloading BtbN static ffmpeg (full GPL build)...")
        urllib.request.urlretrieve(url, tmp)
        log.info("Extracting...")
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
        return static_dir

    if not _has_drawtext(config.FFMPEG_BIN):
        static_dir = "/app/ffmpeg-static"
        static_ffmpeg = os.path.join(static_dir, "ffmpeg")
        static_ffprobe = os.path.join(static_dir, "ffprobe")

        if not os.path.isfile(static_ffmpeg) or not _has_drawtext(static_ffmpeg):
            _download_btbn()

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

    # ── Load plugins ──────────────────────────────────────────────────────────
    # Pyrogram discovers handlers via @Client.on_* decorators when the modules
    # are imported.  Import order matters: encode (group=0) must be imported
    # before autoencode (group=1) so the group numbers reflect the intended
    # dispatch order.
    import plugins.encode        # noqa: F401 – registers group=0 video handler
    import plugins.autoencode    # noqa: F401 – registers group=1 auto handler
    import plugins.settings      # noqa: F401
    import plugins.queue         # noqa: F401
    import plugins.admin         # noqa: F401
    import plugins.start         # noqa: F401
    import plugins.ocean         # noqa: F401
    import plugins.thumb         # noqa: F401 – /setthumbnail command
    import plugins.group_access  # noqa: F401 – owner group/premium management

    async with app:
        await queue_manager.start_worker()
        log.info("Bot started as %s v%s", config.BOT_NAME, config.BOT_VERSION)
        try:
            await idle()
        finally:
            log.info("Stop signal received. Waiting for running jobs to finish before exit...")
            await queue_manager.stop(timeout=3600.0)
            log.info("Bot stopped.")

if __name__ == "__main__":
    # Pyrogram binds its dispatcher tasks to app.loop (captured when the Client
    # was created). asyncio.run() would create a *different* loop, so handlers
    # never run and shutdown crashes with "attached to a different loop".
    # Run on Pyrogram's own loop instead.
    loop = app.loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        pass
