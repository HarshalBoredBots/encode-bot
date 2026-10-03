from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import config
import database

@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    await database.get_user_settings(message.from_user.id)

    text = (
        f"👋 **Welcome to {config.BOT_NAME}!**\n\n"
        "I encode videos to 480p / 720p / 1080p with maximum compression "
        "and zero perceptible quality loss.\n\n"
        "**Quick start**\n"
        "1. Send me a video or video document.\n"
        "2. Review the settings card.\n"
        "3. Tap ▶️ Start Encode.\n\n"
        "Use /settings to customize the encode, /help for the command list."
    )
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⚙️ Settings", callback_data="settings_home")],
                [InlineKeyboardButton("❓ Help", callback_data="show_help")],
            ]
        ),
    )

@Client.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message):
    await message.reply_text(_HELP_TEXT)

@Client.on_callback_query(filters.regex(r"^show_help$"))
async def show_help_cb(client, cq):
    try:
        await cq.message.edit_text(_HELP_TEXT)
    except Exception:
        pass
    await cq.answer()

_HELP_TEXT = (
    "**Commands**\n\n"
    "**User**\n"
    "/start · welcome\n"
    "/help · this message\n"
    "/settings · configure encoding\n"
    "/queue · your active jobs\n"
    "/cancel · cancel your job\n"
    "/status · bot statistics\n\n"
    "**Admin**\n"
    "/broadcast · message all users\n"
    "/ban <id> · ban a user\n"
    "/unban <id> · unban a user\n"
    "/stats · detailed statistics\n"
    "/log · download latest log\n"
    "/shell <cmd> · run shell command (owner)\n"
    "/restart · restart bot (owner)\n"
    "/ocean · import settings from reply (admin)"
)
