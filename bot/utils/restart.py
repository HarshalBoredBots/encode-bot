import os
import sys
import asyncio
from bot.logger import get_logger

log = get_logger(__name__)

async def restart_bot():
    """Gracefully exit so Heroku/systemd restarts the dyno."""
    log.info("Restart requested. Exiting.")
    await asyncio.sleep(1)
    try:
        os.execv(sys.executable, [sys.executable, "-m", "bot"])
    except Exception:
        os._exit(0)
