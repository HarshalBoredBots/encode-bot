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


# ── Auto-encode template storage ─────────────────────────────────────────────

async def set_autoencode_template(user_id: int, template: str) -> None:
    """Save the user's auto-encode filename template. Empty string = disabled."""
    await _db.users.update_one(
        {"_id": user_id},
        {"$set": {"autoencode_template": template}},
        upsert=True,
    )
    _cache.pop(user_id, None)

async def get_autoencode_template(user_id: int) -> str:
    """Return the user's auto-encode template, or '' if not set."""
    doc = await _db.users.find_one({"_id": user_id}, {"autoencode_template": 1})
    if not doc:
        return ""
    return doc.get("autoencode_template", "")


# ── Group access storage ──────────────────────────────────────────────────────

async def add_allowed_group(group_id: int, added_by: int) -> None:
    """Grant a group access to the bot."""
    await _db.groups.update_one(
        {"_id": group_id},
        {"$set": {"allowed": True, "added_by": added_by, "added_at": datetime.utcnow()}},
        upsert=True,
    )

async def remove_allowed_group(group_id: int) -> None:
    """Revoke a group's access."""
    await _db.groups.update_one({"_id": group_id}, {"$set": {"allowed": False}})

async def is_group_allowed(group_id: int) -> bool:
    """Check if a group is allowed."""
    doc = await _db.groups.find_one({"_id": group_id})
    return bool(doc and doc.get("allowed"))

async def list_allowed_groups() -> list:
    """Return list of allowed group IDs."""
    return [d["_id"] async for d in _db.groups.find({"allowed": True})]

async def is_premium_user(user_id: int) -> bool:
    """Check if user has premium access (for DM use)."""
    doc = await _db.users.find_one({"_id": user_id})
    return bool(doc and doc.get("is_premium"))

async def set_premium_user(user_id: int, value: bool) -> None:
    """Grant or revoke premium for a user."""
    await _db.users.update_one(
        {"_id": user_id},
        {"$set": {"is_premium": value}},
        upsert=True,
    )


# ── Persistent job queue ──────────────────────────────────────────────────────
# Jobs are written to MongoDB before starting so they survive dyno restarts.
# On startup __main__.py calls recover_interrupted_jobs() to re-queue anything
# that was "running" when the process was killed.

async def save_job(job_doc: dict) -> None:
    """Upsert a job document (keyed by job_id)."""
    await _db.jobs.update_one(
        {"_id": job_doc["job_id"]},
        {"$set": {**job_doc, "updated_at": datetime.utcnow()}},
        upsert=True,
    )

async def mark_job_done(job_id: str, status: str = "completed") -> None:
    """Mark a job as completed/failed/cancelled so it won't be recovered."""
    await _db.jobs.update_one(
        {"_id": job_id},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}},
    )

async def get_interrupted_jobs() -> list:
    """
    Return all jobs that were 'running' when the bot last died.
    These need to be re-queued on startup.
    """
    cursor = _db.jobs.find({"status": "running"})
    return await cursor.to_list(length=None)

async def ensure_job_indexes() -> None:
    try:
        await _db.jobs.create_index("status")
        await _db.jobs.create_index("updated_at", expireAfterSeconds=86400 * 7)  # TTL 7 days
    except Exception:
        pass
