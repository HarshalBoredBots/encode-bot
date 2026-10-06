import time
from bot.utils.format import humanbytes, TimeFormatter

_last_update: dict = {}


async def progress_for_pyrogram(current, total, ud_type, message, start):
    """
    Universal download/upload progress callback for Pyrogram.
    Shows a clean bar with speed and ETA. Rate-limited to once every 4 s.
    """
    if message is None or not total or total <= 0:
        return

    now  = time.time()
    key  = (getattr(message, "chat", None) and message.chat.id, message.id, ud_type)
    last = _last_update.get(key, 0.0)

    # Throttle — but always send the final 100 % update
    if current < total and now - last < 4.0:
        return
    _last_update[key] = now

    elapsed    = max(now - start, 0.001)
    speed      = current / elapsed          # bytes/s
    pct        = current * 100 / total
    eta        = (total - current) / speed if speed > 0 else 0
    filled     = int(pct // 10)
    bar        = "█" * filled + "─" * (10 - filled)

    # Choose icon based on direction
    if "upload" in ud_type.lower() or "⬆" in ud_type:
        icon = "⬆️"
    elif "download" in ud_type.lower() or "⬇" in ud_type:
        icon = "⬇️"
    else:
        icon = "🔄"

    text = (
        f"{icon} <b>{ud_type}</b>\n"
        f"<code>[{bar}]</code> <b>{pct:.1f}%</b>\n"
        f"📦 {humanbytes(current)} / {humanbytes(total)}\n"
        f"⚡ {humanbytes(speed)}/s  ·  ⏱ ETA: {TimeFormatter(int(eta))}"
    )
    try:
        await message.edit_text(text, parse_mode="html")
    except Exception:
        pass
