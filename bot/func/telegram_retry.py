"""
Retry helper for Telegram API calls that fail with transient DC errors.

Handles:
  - RANDOM_ID_DUPLICATE  — means Telegram already received the message; treat as success
  - INTERDC_X_CALL_RICH_ERROR  (Telegram inter-DC failure, usually DC5)
  - FloodWait              (honour the wait time)
"""

import asyncio
import logging

log = logging.getLogger(__name__)

# Errors where retrying with a fresh call makes sense
_RETRYABLE = {
    "INTERDC_1_CALL_RICH_ERROR",
    "INTERDC_2_CALL_RICH_ERROR",
    "INTERDC_3_CALL_RICH_ERROR",
    "INTERDC_4_CALL_RICH_ERROR",
    "INTERDC_5_CALL_RICH_ERROR",
    "INTERDC_X_CALL_RICH_ERROR",
}

# Errors that mean Telegram already processed the request — don't retry, return None
_ALREADY_SENT = {
    "RANDOM_ID_DUPLICATE",
}

MAX_RETRIES = 5
BASE_DELAY  = 2.0   # seconds; doubles each attempt


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc)
    if any(tag in msg for tag in _RETRYABLE):
        return True
    # pyrofork / pyrogram FloodWait has a .value attribute
    if "FloodWait" in type(exc).__name__:
        return True
    return False


def _is_already_sent(exc: Exception) -> bool:
    msg = str(exc)
    return any(tag in msg for tag in _ALREADY_SENT)


async def tg_call(coro_factory, *, retries: int = MAX_RETRIES):
    """
    Call ``coro_factory()`` (a zero-arg async callable that returns a coroutine)
    and retry on transient Telegram errors with exponential back-off.

    RANDOM_ID_DUPLICATE is treated as a silent success — it means Telegram already
    received and processed the message on a previous attempt; retrying would send it
    again (or keep failing), so we return None instead.

    Usage::

        result = await tg_call(lambda: client.send_document(chat_id, path, ...))
    """
    delay = BASE_DELAY
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            name = type(exc).__name__

            # RANDOM_ID_DUPLICATE — Telegram already has the message, stop retrying
            if _is_already_sent(exc):
                log.info(
                    "RANDOM_ID_DUPLICATE on attempt %d — message already delivered, skipping retry",
                    attempt + 1,
                )
                return None

            # FloodWait: sleep exactly as long as Telegram demands
            if "FloodWait" in name:
                wait = getattr(exc, "value", delay)
                log.warning("FloodWait %ds on attempt %d — sleeping", wait, attempt + 1)
                await asyncio.sleep(wait)
                delay = BASE_DELAY  # reset after flood
                continue

            if _is_retryable(exc) and attempt < retries:
                log.warning(
                    "Retryable Telegram error on attempt %d/%d: %s — retrying in %.1fs",
                    attempt + 1, retries, exc, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue

            raise
    raise last_exc
