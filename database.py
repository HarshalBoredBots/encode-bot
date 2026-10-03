import time
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient

from bot import config
from bot.utils.settings import normalize_settings

_client = None
_db = None

_cache: dict = {}
_cache_ttl = 30
_CACHE_MAX = 500

async def init_db():
    global _client, _db
    _client = AsyncIOMotorClient(config.DATABASE_URL)
    _db = _client[config.DATABASE_NAME]
    try:
        await _db.users.create_index("last_active")
    except Exception:
        pass
    return _db

def db():
    return _db

async def get_user_settings(user_id: int) -> dict:
    now = time.time()
    cached = _cache.get(user_id)
    if cached and now - cached[0] < _cache_ttl:
        return cached[1]

    doc = await _db.users.find_one({"_id": user_id})
    settings = normalize_settings((doc or {}).get("settings"))

    if len(_cache) >= _CACHE_MAX:
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
        _cache.pop(oldest, None)

    _cache[user_id] = (now, settings)
    return settings

async def update_user_settings(user_id: int, settings: dict) -> None:
    s = normalize_settings(settings)
    now = datetime.utcnow()
    await _db.users.update_one(
        {"_id": user_id},
        {
            "$set": {"settings": s, "last_active": now},
            "$setOnInsert": {"first_seen": now},
        },
        upsert=True,
    )
    _cache[user_id] = (time.time(), s)

async def full_userbase():
    return [d["_id"] async for d in _db.users.find({}, {"_id": 1})]

async def get_variable(key: str, default=None):
    doc = await _db.config.find_one({"_id": key})
    return doc["value"] if doc else default

async def set_variable(key: str, value) -> None:
    await _db.config.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)

async def inc_stats(field: str, amount: int = 1) -> None:
    await _db.config.update_one(
        {"_id": "stats"}, {"$inc": {field: amount}}, upsert=True
    )

async def get_stats() -> dict:
    doc = await _db.config.find_one({"_id": "stats"}) or {}
    doc.pop("_id", None)
    return doc

async def is_user_banned(user_id: int) -> bool:
    return bool(await get_variable(f"banned_{user_id}", False))

async def ban_user(user_id: int) -> None:
    await set_variable(f"banned_{user_id}", True)

async def unban_user(user_id: int) -> None:
    await set_variable(f"banned_{user_id}", False)
