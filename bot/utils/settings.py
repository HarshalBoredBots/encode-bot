import copy
import os
import re

DEFAULT_FONT_PATH = "bot/fonts/Kufam-SemiBold.ttf"

VALID_CODECS = {"libx264", "libx265", "libvpx-vp9", "libaom-av1"}
VALID_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
]
VALID_AUDIO_CODECS = {"aac", "ac3", "copy"}
VALID_RESOLUTIONS = ("1080p", "720p", "480p", "360p")

WATERMARK_POSITIONS = {
    "top_left":  ("x=P",              "y=P"),
    "top_mid":   ("x=(W-text_w)/2",   "y=P"),
    "top_right": ("x=W-text_w-P",     "y=P"),
    "mid_left":  ("x=P",              "y=(H-text_h)/2"),
    "mid_right": ("x=W-text_w-P",     "y=(H-text_h)/2"),
    "bot_left":  ("x=P",              "y=H-text_h-P"),
    "bot_right": ("x=W-text_w-P",     "y=H-text_h-P"),
}

DEFAULT_SETTINGS = {
    "profile": "custom",
    "video": {
        "crf": "23",
        "preset": "slow",
        "resolution": ["720p"],
        "codec": "libx264",
        "subtitle_mode": "copy",
        "remux": False,
        "sample_seconds": 0,
        "trim_start": 0,
        "trim_end": 0,
        "output_format": "mkv",
    },
    "audio": {
        "bitrate": "128k",
        "codec": "aac",
        "track": "all",
        "normalize": False,
    },
    "metadata": {
        "global": {"title": "", "encoder": "EncoderBot"},
        "video": {},
        "audio": {},
        "subtitle": {},
    },
    "custom_ffmpeg": {},
    "active_custom_ffmpeg": "",
    "rename": {"pattern": ""},
    "output_as_video": True,
    "watermark": {
        "enabled": False,
        "text": "Encoded",
        "color": "white",
        "font_size": 24,
        "padding": 5,
        "position": "bot_right",
        "font_name": "Roboto",
        "font_path": None,
        "timing_mode": "full",
        "start": 0,
        "end": 0,
        "repeat_count": 3,
        "duration": 5,
    },
    "thumbnail": None,
}

QUALITY_PRESETS = {
    "fast": {
        "label": "Fast",
        "description": "H.264 · veryfast · 128k · quick encode",
        "video": {"codec": "libx264", "crf": "23", "preset": "veryfast"},
        "audio": {"codec": "aac", "bitrate": "128k"},
    },
    "balanced": {
        "label": "Balanced",
        "description": "H.264 · medium · 128k · good balance",
        "video": {"codec": "libx264", "crf": "22", "preset": "medium"},
        "audio": {"codec": "aac", "bitrate": "128k"},
    },
    "compact": {
        "label": "Compact",
        "description": "HEVC · slow · 128k · smallest file",
        "video": {"codec": "libx265", "crf": "28", "preset": "slow"},
        "audio": {"codec": "aac", "bitrate": "128k"},
    },
}

def _deep_merge(base, override):
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result

def normalize_settings(raw) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    s = _deep_merge(DEFAULT_SETTINGS, raw)

    video = s.setdefault("video", {})
    if video.get("codec") not in VALID_CODECS:
        video["codec"] = "libx264"
    try:
        crf = int(video.get("crf", 23))
    except (TypeError, ValueError):
        crf = 23
    video["crf"] = str(max(0, min(51, crf)))
    if video.get("preset") not in VALID_PRESETS:
        video["preset"] = "slow"

    res = video.get("resolution") or ["720p"]
    if isinstance(res, str):
        res = [res]
    res = [r for r in res if r in VALID_RESOLUTIONS]
    if not res:
        res = ["720p"]
    seen = set()
    video["resolution"] = [r for r in res if not (r in seen or seen.add(r))]

    if video.get("subtitle_mode") not in ("copy", "drop"):
        video["subtitle_mode"] = "copy"
    if video.get("output_format") not in ("mkv", "mp4"):
        video["output_format"] = "mkv"

    try:
        video["sample_seconds"] = max(0, min(600, int(video.get("sample_seconds") or 0)))
    except (TypeError, ValueError):
        video["sample_seconds"] = 0
    try:
        video["trim_start"] = max(0.0, float(video.get("trim_start") or 0))
    except (TypeError, ValueError):
        video["trim_start"] = 0
    try:
        video["trim_end"] = max(0.0, float(video.get("trim_end") or 0))
    except (TypeError, ValueError):
        video["trim_end"] = 0
    video["remux"] = bool(video.get("remux", False))

    audio = s.setdefault("audio", {})
    if audio.get("codec") not in VALID_AUDIO_CODECS:
        audio["codec"] = "aac"
    br = str(audio.get("bitrate", "128k"))
    if not re.fullmatch(r"\d+k", br):
        br = "128k"
    if int(br[:-1]) < 32:
        br = "64k"
    audio["bitrate"] = br
    if audio.get("track") not in ("all", "1", "2", "none"):
        audio["track"] = "all"
    audio["normalize"] = bool(audio.get("normalize", False))

    md = s.setdefault("metadata", {})
    if not isinstance(md.get("global"), dict):
        md["global"] = {"title": "", "encoder": "EncoderBot"}
    md["global"].setdefault("title", "")
    md["global"].setdefault("encoder", "EncoderBot")

    wm = s.setdefault("watermark", {})
    wm["enabled"] = bool(wm.get("enabled", False))
    wm["text"] = str(wm.get("text", "Encoded"))[:200]
    color = str(wm.get("color", "white"))
    if not re.fullmatch(r"[a-zA-Z]+|#[0-9a-fA-F]{3,8}", color):
        color = "white"
    wm["color"] = color
    try:
        fs = int(wm.get("font_size", 24))
    except (TypeError, ValueError):
        fs = 24
    wm["font_size"] = max(10, min(200, fs))
    try:
        pd = int(wm.get("padding", 5))
    except (TypeError, ValueError):
        pd = 5
    wm["padding"] = max(0, min(25, pd))
    valid_pos = set(WATERMARK_POSITIONS.keys())
    if wm.get("position") not in valid_pos:
        wm["position"] = "bot_right"
    if wm.get("timing_mode") not in ("full", "range", "random_duration"):
        wm["timing_mode"] = "full"
    fp = wm.get("font_path")
    wm["font_path"] = fp if fp and os.path.isfile(fp) else None
    wm["font_name"] = str(wm.get("font_name") or "Roboto")
    try:
        wm["start"] = max(0, int(float(wm.get("start") or 0)))
    except (TypeError, ValueError):
        wm["start"] = 0
    try:
        wm["end"] = max(0, int(float(wm.get("end") or 0)))
    except (TypeError, ValueError):
        wm["end"] = 0
    try:
        wm["repeat_count"] = max(1, min(50, int(wm.get("repeat_count") or 3)))
    except (TypeError, ValueError):
        wm["repeat_count"] = 3
    try:
        wm["duration"] = max(1, min(3600, int(wm.get("duration") or 5)))
    except (TypeError, ValueError):
        wm["duration"] = 5

    if not isinstance(s.get("custom_ffmpeg"), dict):
        s["custom_ffmpeg"] = {}
    if not isinstance(s.get("active_custom_ffmpeg"), str):
        s["active_custom_ffmpeg"] = ""

    if not isinstance(s.get("rename"), dict):
        s["rename"] = {"pattern": ""}
    s["rename"]["pattern"] = str(s["rename"].get("pattern") or "")[:120]

    s["output_as_video"] = bool(s.get("output_as_video", True))
    # thumbnail is a Telegram file_id string — no filesystem check needed
    if s.get("thumbnail") and not isinstance(s["thumbnail"], str):
        s["thumbnail"] = None

    return s

def apply_quality_preset(settings, profile: str) -> dict:
    preset = QUALITY_PRESETS.get(profile)
    s = normalize_settings(settings)
    if not preset:
        return s
    s["profile"] = profile
    for k, v in (preset.get("video") or {}).items():
        s["video"][k] = v
    for k, v in (preset.get("audio") or {}).items():
        s["audio"][k] = v
    return normalize_settings(s)
