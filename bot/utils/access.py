from pyrogram.enums import ChatType
from bot import config
import database


async def check_access(message) -> tuple:
    """
    Returns (allowed: bool, reason: str).
    Call this at the start of every command and media handler.
    """
    user_id = message.from_user.id if message.from_user else None
    chat = message.chat

    # Owner always allowed everywhere
    if user_id and user_id == config.OWNER_ID:
        return True, ""

    is_private = chat.type == ChatType.PRIVATE
    is_group = chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_private:
        if user_id and await database.is_premium_user(user_id):
            return True, ""
        return False, (
            "🔒 This bot is only available in approved groups.\n"
            "Contact the owner if you need private access."
        )

    if is_group:
        if await database.is_group_allowed(chat.id):
            return True, ""
        return False, (
            "⛔ This group does not have access to the bot.\n"
            "Ask the owner to approve this group with /addgroup."
        )

    return False, "❌ Unsupported chat type."
