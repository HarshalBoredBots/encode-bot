import asyncio
import os
import time

from bot import config
from bot.func.pyroutils.progress import progress_for_pyrogram
from bot.logger import get_logger

log = get_logger(__name__)

_dl_sem = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)

async def download(client, file_id: str, destination: str, message=None, ud_type: str = "⬇️ Downloading..."):
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    async with _dl_sem:
        start = time.time()
        kwargs = {
            "file_name": destination,
        }
        if message is not None:
            kwargs["progress"] = progress_for_pyrogram
            kwargs["progress_args"] = (ud_type, message, start)
        path = await client.download_media(file_id, **kwargs)
        log.info("Downloaded %s -> %s", file_id, path)
        return path
