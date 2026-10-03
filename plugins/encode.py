import uuid

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import config
from bot.func.queue_manager import queue_manager, Job
from bot.func.encode import run_encode_job
from bot.utils.format import humanbytes
import database

def _is_video_doc(message):
    if message.video:
        return True
    if message.document:
        mt = (message.document.mime_type or "").lower()
        return mt.startswith("video/")
    return False

@Client.on_message(filters.private & (filters.video | filters.document))
async def on_video(client, message):
    if not _is_video_doc(message):
        return

    user_id = message.from_user.id
    if await database.is_user_banned(user_id):
        await message.reply_text("🚫 You are banned.")
        return

    media = message.video or message.document
    file_size = media.file_size or 0
    if file_size > config.MAX_FILE_SIZE:
        await message.reply_text(
            f"❌ File too large: {humanbytes(file_size)} "
            f"(limit {humanbytes(config.MAX_FILE_SIZE)})."
        )
        return

    if len(queue_manager.get_user_jobs(user_id)) >= config.MAX_JOBS_PER_USER:
        await message.reply_text(
            "⚠️ You already have an active job. Use /queue or /cancel first."
        )
        return

    settings = await database.get_user_settings(user_id)
    v = settings["video"]
    a = settings["audio"]
    wm = settings["watermark"]

    text = (
        f"📁 **{media.file_name or 'video'}** ({humanbytes(file_size)})\n\n"
        f"**Encode settings**\n"
        f"• Codec: `{v['codec']}` · preset `{v['preset']}` · CRF `{v['crf']}`\n"
        f"• Resolution(s): {', '.join(v['resolution'])}\n"
        f"• Audio: {a['codec']} {a['bitrate']}"
        f"{' (track ' + a['track'] + ')' if a['track'] != 'all' else ''}\n"
        f"• Subtitles: {v['subtitle_mode']}\n"
        f"• Output: {v['output_format'].upper()}"
        f"{' (remux)' if v['remux'] else ''}\n"
        f"• Watermark: {'ON' if wm['enabled'] else 'OFF'}\n"
    )
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶️ Start Encode", callback_data=f"enc_start:{message.id}")],
            [
                InlineKeyboardButton("⚙️ Settings", callback_data="settings_home"),
                InlineKeyboardButton("✖️ Cancel", callback_data=f"enc_cancel:{message.id}"),
            ],
        ]
    )
    await message.reply_text(text, reply_markup=markup)

@Client.on_callback_query(filters.regex(r"^enc_start:"))
async def enc_start(client, cq):
    try:
        await cq.answer()
    except Exception:
        pass

    src = cq.message.reply_to_message
    if not src or not src.from_user or src.from_user.id != cq.from_user.id:
        try:
            await cq.answer("Source message not found.", show_alert=True)
        except Exception:
            pass
        return

    media = src.video or src.document
    if not media:
        try:
            await cq.answer("No media found.", show_alert=True)
        except Exception:
            pass
        return

    user_id = cq.from_user.id
    if len(queue_manager.get_user_jobs(user_id)) >= config.MAX_JOBS_PER_USER:
        try:
            await cq.answer("You already have an active job.", show_alert=True)
        except Exception:
            pass
        return

    job = Job(
        job_id=str(uuid.uuid4()),
        user_id=user_id,
        func=run_encode_job,
        file_name=media.file_name or "video",
        file_size=humanbytes(media.file_size or 0),
        chat_id=cq.message.chat.id,
        message_id=cq.message.id,
    )
    job.args = (client, job)
    job.kwargs = {
        "media_ref": media,
        "src_chat_id": src.chat.id,
        "src_message_id": src.id,
    }

    ok, info = await queue_manager.add_job(job)
    if not ok:
        try:
            await cq.answer(info, show_alert=True)
        except Exception:
            pass
        return

    try:
        await cq.message.edit_text(
            f"✅ Added to queue.\nPosition: {info}"
        )
    except Exception:
        pass

@Client.on_callback_query(filters.regex(r"^enc_cancel:"))
async def enc_cancel(client, cq):
    try:
        await cq.message.delete()
    except Exception:
        pass
    try:
        await cq.answer("Cancelled.")
    except Exception:
        pass
