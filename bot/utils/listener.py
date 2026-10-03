import asyncio
from pyrogram import filters
from pyrogram.handlers import MessageHandler

_pending: dict = {}
_loop_lock = asyncio.Lock()

async def _listener_handler(client, message):
    uid = message.from_user.id if message.from_user else None
    if uid is None:
        return
    fut = _pending.get(uid)
    if fut and not fut.done():
        fut.set_result(message)

def register_listener(client):
    client.add_handler(
        MessageHandler(_listener_handler, filters.private), group=-100
    )

async def ask_user(client, chat_id: int, prompt: str, timeout: int = 60, user_id: int = None):
    if user_id is None:
        user_id = chat_id
    await client.send_message(chat_id, prompt)
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    _pending[user_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        try:
            await client.send_message(chat_id, "No response received. Setting unchanged.")
        except Exception:
            pass
        return None
    finally:
        _pending.pop(user_id, None)
