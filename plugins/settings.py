import os
import re

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import config
from bot.utils.settings import (
    normalize_settings,
    apply_quality_preset,
    QUALITY_PRESETS,
    VALID_CODECS,
    VALID_PRESETS,
    VALID_RESOLUTIONS,
)
from bot.utils.listener import ask_user
import database
from bot.utils.access import check_access

def _b(text, data):
    return InlineKeyboardButton(text, callback_data=data)

def _cb(text, data, active=False):
    prefix = "✅ " if active else ""
    return InlineKeyboardButton(prefix + text, callback_data=data)

def main_text(s):
    v, a, wm = s["video"], s["audio"], s["watermark"]
    return (
        f"⚙️ **Settings**\n\n"
        f"**Video**\n"
        f"• Codec: `{v['codec']}`\n"
        f"• Preset: `{v['preset']}` · CRF `{v['crf']}`\n"
        f"• Resolution: {', '.join(v['resolution'])}\n"
        f"• Subtitles: {v['subtitle_mode']}\n"
        f"• Format: {v['output_format'].upper()}"
        f" · Remux: {'ON' if v['remux'] else 'OFF'}\n\n"
        f"**Audio**\n"
        f"• {a['codec']} {a['bitrate']} · track: {a['track']}"
        f" · normalize: {'on' if a['normalize'] else 'off'}\n\n"
        f"**Other**\n"
        f"• Send as: {'Video' if s['output_as_video'] else 'Document'}\n"
        f"• Watermark: {'ON' if wm['enabled'] else 'OFF'}\n"
        f"• Rename: {s['rename']['pattern'] or 'none'}\n"
        f"• Sample: {v['sample_seconds']}s\n"
        f"• Trim: start {v['trim_start']}s → end {v['trim_end']}s\n"
    )

def main_kb(s):
    v, a, wm = s["video"], s["audio"], s["watermark"]
    rows = [
        [_b(f"🎬 Codec: {v['codec']}", "s:codec"),
         _b(f"⚡ Preset: {v['preset']}", "s:preset")],
        [_b(f"📐 Resolution: {','.join(v['resolution'])}", "s:res")],
        [_b(f"🎚 CRF: {v['crf']}", "s:crf")],
        [_b(f"🔊 Audio: {a['codec']} {a['bitrate']}", "s:audio")],
        [_b(f"💬 Subtitles: {v['subtitle_mode']}", "s:subs")],
        [_b(f"📦 Format: {v['output_format'].upper()}", "s:fmt"),
         _b(f"🔁 Remux: {'ON' if v['remux'] else 'OFF'}", "s:remux")],
        [_b(f"📤 Send as: {'Video' if s['output_as_video'] else 'Document'}", "s:sendas")],
        [_b("⚡ Quick Presets", "s:presets")],
        [_b(f"💧 Watermark: {'ON' if wm['enabled'] else 'OFF'}", "s:wm")],
        [_b(f"📝 Rename pattern", "s:rename"),
         _b("🧩 Custom FFmpeg", "s:custom")],
        [_b(f"✂️ Trim ({v['trim_start']}-{v['trim_end']})", "s:trim"),
         _b(f"🎞 Sample: {v['sample_seconds']}s", "s:sample")],
        [_b("✖️ Close", "s:close")],
    ]
    return InlineKeyboardMarkup(rows)

async def _render(cq, text, kb):
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass

async def _save(user_id, settings):
    await database.update_user_settings(user_id, settings)

@Client.on_message(filters.command("settings"))
async def settings_cmd(client, message):
    allowed, reason = await check_access(message)
    if not allowed:
        if message.chat.type == "private":
            await message.reply_text(reason)
        return
    s = await database.get_user_settings(message.from_user.id)
    await message.reply_text(main_text(s), reply_markup=main_kb(s))

@Client.on_callback_query(filters.regex(r"^settings_home$"))
async def settings_home(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    await _render(cq, main_text(s), main_kb(s))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^s:close$"))
async def s_close(client, cq):
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^s:codec$"))
async def s_codec(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = s["video"]["codec"]
    kb = InlineKeyboardMarkup([
        [_cb("H.264 (libx264)", "sc:libx264", cur == "libx264"),
         _cb("H.265 (libx265)", "sc:libx265", cur == "libx265")],
        [_cb("VP9 (libvpx-vp9)", "sc:libvpx-vp9", cur == "libvpx-vp9"),
         _cb("SVT-AV1 (libsvtav1)", "sc:libsvtav1", cur == "libsvtav1")],
        [_b("◀️ Back", "settings_home")],
    ])
    await _render(cq, f"🎬 **Video codec** — current: `{cur}`", kb)
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^sc:"))
async def sc_set(client, cq):
    codec = cq.data.split(":", 1)[1]
    if codec not in VALID_CODECS:
        await cq.answer("Invalid codec.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["video"]["codec"] = codec
    await _save(cq.from_user.id, s)
    await settings_home(client, cq)

@Client.on_callback_query(filters.regex(r"^s:preset$"))
async def s_preset(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = s["video"]["preset"]
    codec = s["video"]["codec"]
    rows = []
    if codec == "libsvtav1":
        # SVT-AV1 uses numeric presets 0-8 (0=slowest/best, 8=fastest)
        svt_presets = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
        row = []
        for p in svt_presets:
            row.append(_cb(p, f"sp:{p}", p == cur))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        hint = "SVT-AV1 presets: 0=best quality/slowest · 4=balanced · 8=fastest\nRecommended: 4"
    else:
        row = []
        for p in VALID_PRESETS:
            row.append(_cb(p, f"sp:{p}", p == cur))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        hint = "Slower presets give smaller files but take longer."
    rows.append([_b("◀️ Back", "settings_home")])
    await _render(cq, f"⚡ **Preset** — current: `{cur}`\n\n{hint}", InlineKeyboardMarkup(rows))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^sp:"))
async def sp_set(client, cq):
    preset = cq.data.split(":", 1)[1]
    if preset not in VALID_PRESETS:
        await cq.answer("Invalid preset.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["video"]["preset"] = preset
    await _save(cq.from_user.id, s)
    await settings_home(client, cq)

@Client.on_callback_query(filters.regex(r"^s:crf$"))
async def s_crf(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = int(s["video"]["crf"])
    kb = InlineKeyboardMarkup([
        [_b("−5", "scrfd:-5"), _b("−1", "scrfd:-1"),
         _b(f"CRF {cur}", "noop"),
         _b("+1", "scrfd:1"), _b("+5", "scrfd:5")],
        [_b("◀️ Back", "settings_home")],
    ])
    await _render(
        cq,
        f"🎚 **CRF** — current `{cur}`\n\nLower = better quality + bigger file. Recommended 18-28.",
        kb,
    )
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^scrfd:(-?\d+)$"))
async def scrfd(client, cq):
    delta = int(cq.data.split(":")[1])
    s = await database.get_user_settings(cq.from_user.id)
    v = int(s["video"]["crf"]) + delta
    s["video"]["crf"] = str(max(0, min(51, v)))
    await _save(cq.from_user.id, s)
    await s_crf(client, cq)

@Client.on_callback_query(filters.regex(r"^s:res$"))
async def s_res(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    active = set(s["video"]["resolution"])
    rows = []
    for r in VALID_RESOLUTIONS:
        rows.append([_cb(r, f"sres:{r}", r in active)])
    rows.append([_b("◀️ Back", "settings_home")])
    text = "📐 **Resolution** (multi-select)\n\nMultiple resolutions = multiple output files, uploaded sequentially."
    await _render(cq, text, InlineKeyboardMarkup(rows))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^sres:"))
async def sres_toggle(client, cq):
    r = cq.data.split(":", 1)[1]
    if r not in VALID_RESOLUTIONS:
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    cur = set(s["video"]["resolution"])
    if r in cur and len(cur) > 1:
        cur.discard(r)
    else:
        cur.add(r)
    s["video"]["resolution"] = [x for x in VALID_RESOLUTIONS if x in cur]
    await _save(cq.from_user.id, s)
    await s_res(client, cq)

@Client.on_callback_query(filters.regex(r"^s:audio$"))
async def s_audio(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    a = s["audio"]
    rows = [
        [_b(f"Codec: {a['codec']}", "sau:codec")],
        [_b(f"Bitrate: {a['bitrate']}", "sau:br")],
        [_b(f"Track: {a['track']}", "sau:track")],
        [_b(f"Normalize: {'ON' if a['normalize'] else 'OFF'}", "sau:norm")],
        [_b("◀️ Back", "settings_home")],
    ]
    await _render(cq, "🔊 **Audio settings**", InlineKeyboardMarkup(rows))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^sau:codec$"))
async def sau_codec(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = s["audio"]["codec"]
    kb = InlineKeyboardMarkup([
        [_cb("AAC", "saucc:aac", cur == "aac"),
         _cb("AC3", "saucc:ac3", cur == "ac3"),
         _cb("Copy", "saucc:copy", cur == "copy")],
        [_b("◀️ Back", "s:audio")],
    ])
    await _render(cq, "🔊 **Audio codec**", kb)
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^saucc:"))
async def saucc_set(client, cq):
    v = cq.data.split(":", 1)[1]
    if v not in ("aac", "ac3", "copy"):
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["audio"]["codec"] = v
    await _save(cq.from_user.id, s)
    await s_audio(client, cq)

@Client.on_callback_query(filters.regex(r"^sau:br$"))
async def sau_br(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = s["audio"]["bitrate"]
    brs = ["64k", "96k", "128k", "192k", "256k", "320k"]
    row = [_cb(b, f"saub:{b}", b == cur) for b in brs]
    rows = [row[:3], row[3:], [_b("◀️ Back", "s:audio")]]
    await _render(cq, f"🔊 **Audio bitrate** — current: `{cur}`", InlineKeyboardMarkup(rows))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^saub:"))
async def saub_set(client, cq):
    v = cq.data.split(":", 1)[1]
    if v not in ("64k", "96k", "128k", "192k", "256k", "320k"):
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["audio"]["bitrate"] = v
    await _save(cq.from_user.id, s)
    await s_audio(client, cq)

@Client.on_callback_query(filters.regex(r"^sau:track$"))
async def sau_track(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = s["audio"]["track"]
    kb = InlineKeyboardMarkup([
        [_cb("All", "saut:all", cur == "all"),
         _cb("Track 1", "saut:1", cur == "1"),
         _cb("Track 2", "saut:2", cur == "2"),
         _cb("None", "saut:none", cur == "none")],
        [_b("◀️ Back", "s:audio")],
    ])
    await _render(cq, "🔊 **Audio track**", kb)
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^saut:"))
async def saut_set(client, cq):
    v = cq.data.split(":", 1)[1]
    if v not in ("all", "1", "2", "none"):
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["audio"]["track"] = v
    await _save(cq.from_user.id, s)
    await s_audio(client, cq)

@Client.on_callback_query(filters.regex(r"^sau:norm$"))
async def sau_norm(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    s["audio"]["normalize"] = not s["audio"]["normalize"]
    await _save(cq.from_user.id, s)
    await s_audio(client, cq)

@Client.on_callback_query(filters.regex(r"^s:subs$"))
async def s_subs(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = s["video"]["subtitle_mode"]
    kb = InlineKeyboardMarkup([
        [_cb("Copy", "ssubs:copy", cur == "copy"),
         _cb("Drop", "ssubs:drop", cur == "drop")],
        [_b("◀️ Back", "settings_home")],
    ])
    await _render(cq, "💬 **Subtitles**\n\nImage-based subs (PGS/DVD) are dropped in MP4 output.", kb)
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^ssubs:"))
async def ssubs_set(client, cq):
    v = cq.data.split(":", 1)[1]
    if v not in ("copy", "drop"):
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["video"]["subtitle_mode"] = v
    await _save(cq.from_user.id, s)
    await settings_home(client, cq)

@Client.on_callback_query(filters.regex(r"^s:fmt$"))
async def s_fmt(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    cur = s["video"]["output_format"]
    kb = InlineKeyboardMarkup([
        [_cb("MKV", "sfmt:mkv", cur == "mkv"),
         _cb("MP4", "sfmt:mp4", cur == "mp4")],
        [_b("◀️ Back", "settings_home")],
    ])
    await _render(cq, "📦 **Output format**\n\nMKV supports all subtitle types. MP4 is more compatible with players.", kb)
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^sfmt:"))
async def sfmt_set(client, cq):
    v = cq.data.split(":", 1)[1]
    if v not in ("mkv", "mp4"):
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["video"]["output_format"] = v
    await _save(cq.from_user.id, s)
    await settings_home(client, cq)

@Client.on_callback_query(filters.regex(r"^s:sendas$"))
async def s_sendas(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    s["output_as_video"] = not s["output_as_video"]
    await _save(cq.from_user.id, s)
    await settings_home(client, cq)

@Client.on_callback_query(filters.regex(r"^s:remux$"))
async def s_remux(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    s["video"]["remux"] = not s["video"]["remux"]
    await _save(cq.from_user.id, s)
    await settings_home(client, cq)

@Client.on_callback_query(filters.regex(r"^s:presets$"))
async def s_presets(client, cq):
    rows = []
    for key, p in QUALITY_PRESETS.items():
        rows.append([_b(f"{p['label']} · {p['description']}", f"sqp:{key}")])
    rows.append([_b("◀️ Back", "settings_home")])
    await _render(cq, "⚡ **Quick Presets**", InlineKeyboardMarkup(rows))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^sqp:"))
async def sqp_apply(client, cq):
    key = cq.data.split(":", 1)[1]
    s = await database.get_user_settings(cq.from_user.id)
    s = apply_quality_preset(s, key)
    await _save(cq.from_user.id, s)
    await settings_home(client, cq)

@Client.on_callback_query(filters.regex(r"^s:rename$"))
async def s_rename(client, cq):
    await cq.answer()
    s = await database.get_user_settings(cq.from_user.id)
    msg = await ask_user(
        client,
        cq.message.chat.id,
        "Send a rename pattern.\n\n"
        "Variables: `{original}`, `{res}`, `{codec}`, `{date}`\n"
        "Example: `{original}_{res}`",
        timeout=60,
        user_id=cq.from_user.id,
    )
    if msg is None:
        return
    s["rename"]["pattern"] = (msg.text or "").strip()[:120]
    await _save(cq.from_user.id, s)
    s2 = await database.get_user_settings(cq.from_user.id)
    await _render(cq, main_text(s2), main_kb(s2))

@Client.on_callback_query(filters.regex(r"^s:trim$"))
async def s_trim(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    s = await database.get_user_settings(uid)
    msg = await ask_user(
        client, cq.message.chat.id,
        "Send trim range as `start,end` in seconds (e.g. `10,120`). Send `0,0` to disable.",
        timeout=60, user_id=uid,
    )
    if msg is None:
        return
    text = (msg.text or "").strip()
    try:
        parts = [p.strip() for p in text.split(",")]
        start = max(0.0, float(parts[0]))
        end = max(0.0, float(parts[1]))
    except Exception:
        await client.send_message(cq.message.chat.id, "Invalid format.")
        return
    s["video"]["trim_start"] = start
    s["video"]["trim_end"] = end
    await _save(uid, s)
    s2 = await database.get_user_settings(uid)
    await _render(cq, main_text(s2), main_kb(s2))

@Client.on_callback_query(filters.regex(r"^s:sample$"))
async def s_sample(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    s = await database.get_user_settings(uid)
    msg = await ask_user(
        client, cq.message.chat.id,
        "Send sample seconds (10-600), or `0` to disable.",
        timeout=60, user_id=uid,
    )
    if msg is None:
        return
    try:
        n = int((msg.text or "0").strip())
    except ValueError:
        await client.send_message(cq.message.chat.id, "Invalid number.")
        return
    s["video"]["sample_seconds"] = max(0, min(600, n))
    await _save(uid, s)
    s2 = await database.get_user_settings(uid)
    await _render(cq, main_text(s2), main_kb(s2))

@Client.on_callback_query(filters.regex(r"^s:custom$"))
async def s_custom(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    custom = s.get("custom_ffmpeg") or {}
    active = s.get("active_custom_ffmpeg") or ""
    rows = []
    for name in custom:
        label = ("✅ " if name == active else "") + name
        rows.append([
            _b(label, f"sfc:act:{name}"),
            _b("🗑", f"sfc:del:{name}"),
        ])
    rows.append([_b("➕ Add New", "sfc:add")])
    rows.append([_b("◀️ Back", "settings_home")])
    text = "🧩 **Custom FFmpeg**\n\nAppended to generated command. Only safe flags allowed."
    await _render(cq, text, InlineKeyboardMarkup(rows))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^sfc:add$"))
async def sfc_add(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    name_msg = await ask_user(client, cq.message.chat.id, "Send a name for the custom command.", timeout=60, user_id=uid)
    if not name_msg or not name_msg.text:
        return
    name = name_msg.text.strip()[:32]
    flags_msg = await ask_user(client, cq.message.chat.id, "Send the flags (e.g. `-movflags +faststart`).", timeout=60, user_id=uid)
    if not flags_msg or not flags_msg.text:
        return
    flags = flags_msg.text.strip()
    from bot.func.ffmpeg_utils import validate_ffmpeg_command
    if not validate_ffmpeg_command(flags):
        await client.send_message(cq.message.chat.id, "❌ Flags rejected (unsafe or unknown).")
        return
    s = await database.get_user_settings(uid)
    s.setdefault("custom_ffmpeg", {})[name] = flags
    await _save(uid, s)
    await client.send_message(cq.message.chat.id, f"✅ Saved `{name}`.")

@Client.on_callback_query(filters.regex(r"^sfc:del:(.+)$"))
async def sfc_del(client, cq):
    name = cq.data.split(":", 2)[2]
    s = await database.get_user_settings(cq.from_user.id)
    s.get("custom_ffmpeg", {}).pop(name, None)
    if s.get("active_custom_ffmpeg") == name:
        s["active_custom_ffmpeg"] = ""
    await _save(cq.from_user.id, s)
    await s_custom(client, cq)

@Client.on_callback_query(filters.regex(r"^sfc:act:(.+)$"))
async def sfc_act(client, cq):
    name = cq.data.split(":", 2)[2]
    s = await database.get_user_settings(cq.from_user.id)
    if s.get("active_custom_ffmpeg") == name:
        s["active_custom_ffmpeg"] = ""
    else:
        s["active_custom_ffmpeg"] = name
    await _save(cq.from_user.id, s)
    await s_custom(client, cq)

_WM_POS_LABELS = {
    "top_left":  "↖️ Top Left",
    "top_mid":   "⬆️ Top Mid",
    "top_right": "↗️ Top Right",
    "mid_left":  "⬅️ Mid Left",
    "mid_right": "➡️ Mid Right",
    "bot_left":  "↙️ Bot Left",
    "bot_right": "↘️ Bot Right",
}

def _wm_kb(wm):
    rows = []
    rows.append([_b(f"Status: {'ENABLED' if wm['enabled'] else 'DISABLED'}", "swm:toggle")])
    rows.append([_b(f"Text: {wm['text']}", "swm:text"),
                 _b(f"Color: {wm['color']}", "swm:color")])
    rows.append([_b(f"Font Size: {wm['font_size']} px", "swm:fsize"),
                 _b(f"Padding: {wm['padding']}%", "swm:pad")])
    rows.append([_b(f"Font: {wm['font_name']}", "swm:font_noop"),
                 _b("Upload Font", "swm:fontup")])
    def lbl(p):
        return ("✅ " if wm["position"] == p else "") + _WM_POS_LABELS[p]
    rows.append([_b(lbl("top_left"), "swm:pos:top_left"),
                 _b(lbl("top_mid"), "swm:pos:top_mid"),
                 _b(lbl("top_right"), "swm:pos:top_right")])
    rows.append([_b(lbl("mid_left"), "swm:pos:mid_left"),
                 _b("      ", "noop"),
                 _b(lbl("mid_right"), "swm:pos:mid_right")])
    rows.append([_b(lbl("bot_left"), "swm:pos:bot_left"),
                 _b("      ", "noop"),
                 _b(lbl("bot_right"), "swm:pos:bot_right")])
    tm = wm["timing_mode"]
    rows.append([
        _cb("Full Video", "swm:tm:full", tm == "full"),
        _cb("Time Range", "swm:tm:range", tm == "range"),
        _cb("Random", "swm:tm:random_duration", tm == "random_duration"),
    ])
    if tm == "range":
        rows.append([
            _b(f"Start: {wm['start']}s", "swm:start"),
            _b(f"End: {wm['end']}s", "swm:end"),
        ])
    if tm == "random_duration":
        rows.append([
            _b(f"Repeats: {wm['repeat_count']}", "swm:repeats"),
            _b(f"Duration: {wm['duration']}s", "swm:dur"),
        ])
    rows.append([_b("◀️ Back", "settings_home")])
    return InlineKeyboardMarkup(rows)

def _wm_text(wm):
    return (
        "💧 **Watermark settings**\n\n"
        f"• Status: {'Enabled' if wm['enabled'] else 'Disabled'}\n"
        f"• Text: `{wm['text']}`\n"
        f"• Color: `{wm['color']}`\n"
        f"• Font size: {wm['font_size']} px\n"
        f"• Padding: {wm['padding']}%\n"
        f"• Position: {wm['position']}\n"
        f"• Font: {wm['font_name']}\n"
        f"• Timing: {wm['timing_mode']}"
    )

@Client.on_callback_query(filters.regex(r"^s:wm$"))
async def s_wm(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    wm = s["watermark"]
    await _render(cq, _wm_text(wm), _wm_kb(wm))
    await cq.answer()

@Client.on_callback_query(filters.regex(r"^swm:toggle$"))
async def swm_toggle(client, cq):
    s = await database.get_user_settings(cq.from_user.id)
    s["watermark"]["enabled"] = not s["watermark"]["enabled"]
    await _save(cq.from_user.id, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:text$"))
async def swm_text(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send watermark text (max 60 chars).", timeout=60, user_id=uid)
    if msg is None:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["text"] = (msg.text or "Encoded").strip()[:60]
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:color$"))
async def swm_color(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send color (e.g. `white`, `#ff0000`).", timeout=60, user_id=uid)
    if msg is None:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["color"] = (msg.text or "white").strip()
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:fsize$"))
async def swm_fsize(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send font size (10-200).", timeout=60, user_id=uid)
    if msg is None:
        return
    try:
        n = int((msg.text or "").strip())
    except ValueError:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["font_size"] = max(10, min(200, n))
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:pad$"))
async def swm_pad(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send padding percentage (0-25).", timeout=60, user_id=uid)
    if msg is None:
        return
    try:
        n = int((msg.text or "").strip())
    except ValueError:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["padding"] = max(0, min(25, n))
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:pos:"))
async def swm_pos(client, cq):
    pos = cq.data.split(":", 2)[2]
    valid = {"top_left","top_mid","top_right","mid_left","mid_right","bot_left","bot_right"}
    if pos not in valid:
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["watermark"]["position"] = pos
    await _save(cq.from_user.id, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:tm:"))
async def swm_tm(client, cq):
    tm = cq.data.split(":", 2)[2]
    if tm not in ("full", "range", "random_duration"):
        await cq.answer("Invalid.", show_alert=True)
        return
    s = await database.get_user_settings(cq.from_user.id)
    s["watermark"]["timing_mode"] = tm
    await _save(cq.from_user.id, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:start$"))
async def swm_start(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send start time (seconds).", timeout=60, user_id=uid)
    if msg is None:
        return
    try:
        n = max(0, int(float((msg.text or "0").strip())))
    except ValueError:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["start"] = n
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:end$"))
async def swm_end(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send end time (seconds).", timeout=60, user_id=uid)
    if msg is None:
        return
    try:
        n = max(0, int(float((msg.text or "0").strip())))
    except ValueError:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["end"] = n
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:repeats$"))
async def swm_repeats(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send repeat count (1-50).", timeout=60, user_id=uid)
    if msg is None:
        return
    try:
        n = max(1, min(50, int((msg.text or "3").strip())))
    except ValueError:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["repeat_count"] = n
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:dur$"))
async def swm_dur(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(client, cq.message.chat.id, "Send each watermark duration in seconds.", timeout=60, user_id=uid)
    if msg is None:
        return
    try:
        n = max(1, min(3600, int((msg.text or "5").strip())))
    except ValueError:
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["duration"] = n
    await _save(uid, s)
    await s_wm(client, cq)

@Client.on_callback_query(filters.regex(r"^swm:fontup$"))
async def swm_fontup(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    msg = await ask_user(
        client, cq.message.chat.id,
        "Send your `.ttf` or `.otf` font file now. (60s timeout)",
        timeout=60, user_id=uid,
    )
    if msg is None or not msg.document:
        return
    name = msg.document.file_name or "font.ttf"
    if not name.lower().endswith((".ttf", ".otf")):
        await client.send_message(cq.message.chat.id, "❌ Only .ttf or .otf files accepted.")
        return
    os.makedirs(config.FONT_DIR, exist_ok=True)
    ext = os.path.splitext(name)[1]
    path = os.path.join(config.FONT_DIR, f"{uid}{ext}")
    try:
        await msg.download(file_name=path)
    except Exception as e:
        await client.send_message(cq.message.chat.id, f"Download failed: {e}")
        return
    s = await database.get_user_settings(uid)
    s["watermark"]["font_path"] = path
    s["watermark"]["font_name"] = name
    await _save(uid, s)
    await client.send_message(cq.message.chat.id, f"✅ Font saved: {name}")

@Client.on_callback_query(filters.regex(r"^swm:font_noop$"))
async def swm_font_noop(client, cq):
    await cq.answer("Use 'Upload Font' to change the font.", show_alert=True)

@Client.on_callback_query(filters.regex(r"^noop$"))
async def noop(client, cq):
    await cq.answer()
