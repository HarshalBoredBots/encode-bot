import asyncio
import os
import time

from bot import config
from bot.func.pyroutils.progress import progress_for_pyrogram
from bot.logger import get_logger

log = get_logger(__name__)

# Telegram only supports streaming video for these formats via send_video
_VIDEO_STREAMING_EXTS = {".mp4", ".mov", ".m4v"}

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
    ext = os.path.splitext(path)[1].lower()
    # Force document upload for formats Telegram can't stream (mkv, avi, etc.)
    if as_video and ext not in _VIDEO_STREAMING_EXTS:
        log.info("Output is %s, switching from send_video to send_document", ext)
        as_video = False

    log.info("Uploading %s to chat %s as %s", path, chat_id, "video" if as_video else "document")

    async with _up_sem:
        start = time.time()
        progress_kwargs = {}
        if message is not None:
            progress_kwargs["progress"] = progress_for_pyrogram
            progress_kwargs["progress_args"] = ("⬆️ Uploading...", message, start)

        try:
            if as_video:
                result = await client.send_video(
                    chat_id,
                    path,
                    thumb=thumb,
                    caption=caption,
                    supports_streaming=True,
                    reply_to_message_id=reply_to,
                    **progress_kwargs,
                )
            else:
                result = await client.send_document(
                    chat_id,
                    path,
                    thumb=thumb,
                    caption=caption,
                    reply_to_message_id=reply_to,
                    **progress_kwargs,
                )
            log.info("Upload complete for %s (%.1fs)", path, time.time() - start)
            return result
        except Exception as e:
            log.exception("Upload failed for %s: %s", path, e)
            raise
