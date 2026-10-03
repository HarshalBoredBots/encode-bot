from pyrogram.types import InlineKeyboardButton

ICONS = {
    "check": "✅",
    "cross": "❌",
    "arrow": "▫️",
    "back": "◀️",
    "close": "✖️",
    "settings": "⚙️",
    "play": "▶️",
    "on": "🟢",
    "off": "🔴",
}

def progress_bar(current, total, length: int = 10) -> str:
    if not total or total <= 0:
        return "[" + "─" * length + "]"
    pct = min(max(current / total, 0.0), 1.0)
    filled = int(pct * length)
    return "[" + "█" * filled + "─" * (length - filled) + f"] {int(pct * 100)}%"

def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)

def back_btn(data="settings_home"):
    return InlineKeyboardButton(f"{ICONS['back']} Back", callback_data=data)

async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return
    except Exception:
        pass
    try:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
    except Exception:
        pass
