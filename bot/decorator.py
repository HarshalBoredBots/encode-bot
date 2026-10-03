import asyncio
from functools import wraps
from bot import config
import database

def is_admin(func):
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        uid = message.from_user.id if getattr(message, "from_user", None) else None
        if uid in config.ADMIN_IDS:
            return await func(client, message, *args, **kwargs)
        try:
            await message.reply_text("Dukhi Atma! 😔")
        except Exception:
            pass
    return wrapper

def is_banned(func):
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        uid = message.from_user.id if getattr(message, "from_user", None) else None
        if uid and await database.is_user_banned(uid):
            try:
                await message.reply_text("🚫 You are banned from using this bot.")
            except Exception:
                pass
            return
        return await func(client, message, *args, **kwargs)
    return wrapper

def task(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return asyncio.create_task(func(*args, **kwargs))
    return wrapper
