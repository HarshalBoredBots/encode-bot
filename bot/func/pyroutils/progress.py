import time
from bot.utils.format import humanbytes as _hb, TimeFormatter as _tf

humanbytes = _hb
TimeFormatter = _tf

_last_update: dict = {}

async def progress_for_pyrogram(current, total, ud_type, message, start):
    if message is None or total is None or total <= 0:
        return
    now = time.time()
    diff = now - start
    key = (getattr(message, "chat", None) and message.chat.id, message.id, ud_type)
    last = _last_update.get(key, 0.0)
    if now - last < 4.0 and current < total:
        return
    _last_update[key] = now

    percentage = current * 100 / total
    speed = current / diff if diff > 0 else 0.0
    eta = (total - current) / speed if speed > 0 else 0.0
    filled = int(percentage // 10)
    bar = "█" * filled + "─" * (10 - filled)

    text = (
        f"{ud_type}\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"{humanbytes(current)} / {humanbytes(total)}\n"
        f"{humanbytes(speed)}/s · ETA: {TimeFormatter(eta)}"
    )
    try:
        await message.edit_text(text)
    except Exception:
        pass
