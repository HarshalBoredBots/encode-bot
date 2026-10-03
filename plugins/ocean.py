import os
import re

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import config
from bot.utils.listener import ask_user
import database

temp_state: dict = {}

_LABEL_TO_POS = {
    "top left": "top_left",
    "top mid": "top_mid",
    "top right": "top_right",
    "mid left": "mid_left",
    "mid right": "mid_right",
    "bot left": "bot_left",
    "bot right": "bot_right",
}

def parse_settings_dump(text: str) -> dict:
    if not text:
        return {}
    text = re.sub(r"<[^>]+>", "", text)
    out = {}

    resolutions = re.findall(r"\b(1080p|720p|480p|360p)\b", text)
    if resolutions:
        seen = set()
        out["resolutions"] = [r for r in resolutions if not (r in seen or seen.add(r))]

    profiles = {}
    for res in ("1080p", "720p", "480p", "360p"):
        m = re.search(
            rf"{res}\s+CRF\s+(\d+)\s*[·|]\s*(\S+)\s*[·|]\s*(\S+)\s*[·|]\s*(\S+)",
            text,
        )
        if m:
            profiles[res] = {
                "crf": m.group(1),
                "preset": m.group(2),
                "codec": m.group(3),
                "bitrate": m.group(4),
            }
    if profiles:
        out["profiles"] = profiles

    m = re.search(r"Send\s+Type\s*[:\-]?\s*(Media|Document)", text, re.I)
    if m:
        out["output_as_video"] = m.group(1).lower() == "media"

    m = re.search(r"Auto\s+Detect\s+Thumb\s*[:\-]?\s*(On|Off)", text, re.I)
    if m:
        out["auto_thumb"] = m.group(1).lower() == "on"

    for field, key in (("Title", "title"), ("Author", "author"), ("Encoder", "encoder")):
        m = re.search(rf"{field}\s*[:\-]\s*(.+)", text)
        if m:
            out.setdefault("metadata", {})[key] = m.group(1).strip()

    m = re.search(r"Start\s+Episode\s*[:\-]?\s*(\d+)", text, re.I)
    if m:
        out["start_episode"] = int(m.group(1))

    wm = {}
    m = re.search(r"Status\s*[:\-]?\s*(Enabled|Disabled)", text, re.I)
    if m:
        wm["enabled"] = m.group(1).lower() == "enabled"
    m = re.search(r"Text\s*[:\-]\s*(.+)", text)
    if m:
        wm["text"] = m.group(1).strip()
    m = re.search(r"Color\s*[:\-]\s*(\S+)", text)
    if m:
        wm["color"] = m.group(1).strip()
    m = re.search(r"Font\s+Size\s*[:\-]?\s*(\d+)", text)
    if m:
        wm["font_size"] = int(m.group(1))
    m = re.search(r"Padding\s*[:\-]?\s*(\d+)%?", text)
    if m:
        wm["padding"] = int(m.group(1))
    for raw_label, key in _LABEL_TO_POS.items():
        if re.search(re.escape(raw_label), text, re.I):
            wm["position"] = key
            break
    m = re.search(r"Font\s*[:\-]\s*(.+)", text)
    if m:
        wm["font_name"] = m.group(1).strip()
    if re.search(r"Full\s+Video", text, re.I):
        wm["timing_mode"] = "full"
    else:
        m = re.search(r"Range\s*[→>]\s*(\d+):(\d+)\s*[→>]\s*(\d+):(\d+)", text)
        if m:
            wm["timing_mode"] = "range"
            wm["start"] = int(m.group(1)) * 60 + int(m.group(2))
            wm["end"] = int(m.group(3)) * 60 + int(m.group(4))
        m = re.search(r"(\d+)\s*[x×]\s*(\d+)\s*s", text)
        if m:
            wm["timing_mode"] = "random_duration"
            wm["repeat_count"] = int(m.group(1))
            wm["duration"] = int(m.group(2))
    if wm:
        out["watermark"] = wm

    return out

async def apply_parsed_settings(user_id: int, parsed: dict) -> list:
    applied = []
    s = await database.get_user_settings(user_id)

    if "resolutions" in parsed and parsed["resolutions"]:
        s["video"]["resolution"] = parsed["resolutions"]
        applied.append(f"Resolutions → {', '.join(parsed['resolutions'])}")

    profiles = parsed.get("profiles") or {}
    if profiles:
        for res in ("1080p", "720p", "480p", "360p"):
            if res in profiles:
                p = profiles[res]
                s["video"]["crf"] = p["crf"]
                s["video"]["preset"] = p["preset"]
                if p["codec"] in ("libx264", "libx265", "libvpx-vp9", "libaom-av1"):
                    s["video"]["codec"] = p["codec"]
                s["audio"]["bitrate"] = p["bitrate"]
                applied.append(f"{res} profile → CRF {p['crf']} · {p['preset']} · {p['codec']} · {p['bitrate']}")
                break

    if "output_as_video" in parsed:
        s["output_as_video"] = parsed["output_as_video"]
        applied.append(f"Send type → {'Media' if parsed['output_as_video'] else 'Document'}")

    md = parsed.get("metadata") or {}
    if md:
        s["metadata"].setdefault("global", {})
        for k, v in md.items():
            s["metadata"]["global"][k] = v
        applied.append("Metadata applied")

    wm = parsed.get("watermark") or {}
    if wm:
        for k, v in wm.items():
            s["watermark"][k] = v
        applied.append("Watermark settings applied")

    await database.update_user_settings(user_id, s)
    return applied

@Client.on_message(filters.command("ocean") & filters.private)
async def cmd_ocean(client, message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.reply_text("Dukhi Atma! 😔")
        return
    if not message.reply_to_message:
        await message.reply_text("Reply to a settings message with /ocean to import.")
        return

    raw_text = (
        message.reply_to_message.text
        or message.reply_to_message.caption
        or ""
    )
    parsed = parse_settings_dump(raw_text)
    if not parsed:
        await message.reply_text("Could not recognise any settings in that message.")
        return

    applied = await apply_parsed_settings(message.from_user.id, parsed)
    temp_state[message.from_user.id] = {
        "state": "await_thumb",
        "applied": applied,
        "chat_id": message.chat.id,
    }

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes", callback_data="ocean:thumb:yes"),
         InlineKeyboardButton("No", callback_data="ocean:thumb:no")],
    ])
    await message.reply_text("Do you have a thumbnail?", reply_markup=kb)

@Client.on_callback_query(filters.regex(r"^ocean:thumb:"))
async def ocean_thumb(client, cq):
    ans = cq.data.split(":", 2)[2]
    uid = cq.from_user.id
    st = temp_state.setdefault(uid, {})
    st["state"] = "await_font"
    st.setdefault("applied", [])
    st["chat_id"] = cq.message.chat.id

    if ans == "yes":
        try:
            await cq.message.edit_text("Send the thumbnail photo now. (60s timeout)")
        except Exception:
            pass
        msg = await ask_user(client, cq.message.chat.id, "Awaiting thumbnail...", timeout=60, user_id=uid)
        if msg and msg.photo:
            os.makedirs(config.THUMB_DIR, exist_ok=True)
            path = os.path.join(config.THUMB_DIR, f"{uid}.jpg")
            try:
                await msg.download(file_name=path)
                s = await database.get_user_settings(uid)
                s["thumbnail"] = path
                await database.update_user_settings(uid, s)
                st["applied"].append("Thumbnail applied")
            except Exception:
                pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes", callback_data="ocean:font:yes"),
         InlineKeyboardButton("No", callback_data="ocean:font:no")],
    ])
    try:
        await cq.message.edit_text("Do you have a custom font file?", reply_markup=kb)
    except Exception:
        await client.send_message(cq.message.chat.id, "Do you have a custom font file?", reply_markup=kb)
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^ocean:font:"))
async def ocean_font(client, cq):
    ans = cq.data.split(":", 2)[2]
    uid = cq.from_user.id
    st = temp_state.get(uid) or {"applied": [], "chat_id": cq.message.chat.id}

    if ans == "yes":
        msg = await ask_user(client, cq.message.chat.id, "Send your .ttf/.otf font now. (60s timeout)", timeout=60, user_id=uid)
        if msg and msg.document:
            name = msg.document.file_name or ""
            if name.lower().endswith((".ttf", ".otf")):
                os.makedirs(config.FONT_DIR, exist_ok=True)
                ext = os.path.splitext(name)[1]
                path = os.path.join(config.FONT_DIR, f"{uid}{ext}")
                try:
                    await msg.download(file_name=path)
                    s = await database.get_user_settings(uid)
                    s["watermark"]["font_path"] = path
                    s["watermark"]["font_name"] = name
                    await database.update_user_settings(uid, s)
                    st["applied"].append(f"Watermark font → {name}")
                except Exception:
                    pass

    applied = st.get("applied") or []
    lines = ["✅ Import complete!", "", "**Applied settings:**"]
    for a in applied:
        lines.append(f"  • {a}")
    await client.send_message(cq.message.chat.id, "\n".join(lines))
    temp_state.pop(uid, None)
    await cq.answer()
