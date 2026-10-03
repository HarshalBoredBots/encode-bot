import asyncio
import time

from bot import config
from bot.func.pyroutils.progress import progress_for_pyrogram
from bot.logger import get_logger

log = get_logger(__name__)

_up_sem = asyncio.Semaphore(config.MAX_CONCURRENT_UPLOADS)

async def upload(
    client,
    chat_id: int,
    path: str,
    thumb: str = None,
    caption: str = "",
    as_video: bool = True,
    reply_to: int = None,
    message=None,
):
    async with _up_sem:
        start = time.time()
        progress_kwargs = {}
        if message is not None:
            progress_kwargs["progress"] = progress_for_pyrogram
            progress_kwargs["progress_args"] = ("⬆️ Uploading...", message, start)

        if as_video:
            return await client.send_video(
                chat_id,
                path,
                thumb=thumb,
                caption=caption,
                supports_streaming=True,
                reply_to_message_id=reply_to,
                **progress_kwargs,
            )
        return await client.send_document(
            chat_id,
            path,
            thumb=thumb,
            caption=caption,
            reply_to_message_id=reply_to,
            **progress_kwargs,
        )
