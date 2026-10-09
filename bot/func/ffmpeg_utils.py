import asyncio
import json
import re

from bot import config
from bot.logger import get_logger
from bot.utils.settings import WATERMARK_POSITIONS, DEFAULT_FONT_PATH

log = get_logger(__name__)

VALID_CODECS = {"libx264", "libx265", "libvpx-vp9", "libsvtav1"}
VALID_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
]
VALID_AUDIO_CODECS = {"aac", "ac3", "copy"}
VALID_RESOLUTIONS = ("1080p", "720p", "480p", "360p")

RES_MAP = {
    "1080p": ("1920", "1080"),
    "720p": ("1280", "720"),
    "480p": ("854", "480"),
    "360p": ("640", "360"),
}

ALLOWED_FLAGS = {
    "-crf", "-preset", "-b:v", "-b:a", "-cpu-used", "-qscale:v",
    "-profile:v", "-level", "-tag:v", "-pix_fmt", "-r", "-g",
    "-keyint_min", "-sc_threshold", "-movflags",
    "-max_muxing_queue_size", "-threads",
    "-metadata", "-metadata:s:v", "-metadata:s:a", "-metadata:s:s",
    "-c:a", "-c:v", "-ac", "-sn", "-an",
    "-bufsize",
}

FORBIDDEN_FLAGS = {"-i", "-map", "-vf", "-filter_complex", "-filter:v", "-filter:a"}

BAD_CHARS_RE = re.compile(r"[;&|`$()<>\\\n\r/]")

def build_watermark_filter(wm: dict, video_duration: float, frame_width: int, frame_height: int) -> str:
    import os
    raw_font = wm.get("font_path") or DEFAULT_FONT_PATH
    font_path = os.path.abspath(raw_font).replace("'", r"\'")
    text = str(wm.get("text", "Encoded")).replace("'", r"\'").replace(":", r"\:")
    color = wm.get("color", "white")
    font_size = int(wm.get("font_size", 24))
    padding_pct = int(wm.get("padding", 5))

    P = max(5, int(min(frame_width or 1280, frame_height or 720) * padding_pct / 100))

    pos_key = wm.get("position", "bot_right")
    x_expr, y_expr = WATERMARK_POSITIONS.get(pos_key, WATERMARK_POSITIONS["bot_right"])
    x_expr = x_expr.replace("P", str(P))
    y_expr = y_expr.replace("P", str(P))

    # x_expr and y_expr from WATERMARK_POSITIONS already include "x=" and "y=" prefixes
    base = (
        f"drawtext=fontfile='{font_path}'"
        f":text='{text}'"
        f":fontcolor={color}"
        f":fontsize={font_size}"
        f":{x_expr}:{y_expr}"
    )

    timing_mode = wm.get("timing_mode", "full")
    if timing_mode == "range":
        start = float(wm.get("start", 0))
        end = float(wm.get("end", 0))
        if end > start:
            base += f":enable='between(t,{start},{end})'"
    elif timing_mode == "random_duration":
        count = max(1, int(wm.get("repeat_count", 3)))
        dur = max(1, int(wm.get("duration", 5)))
        if video_duration and video_duration > 0:
            interval = video_duration / count
            clauses = []
            for i in range(count):
                offset = round(i * interval + interval * 0.3, 2)
                end_t = round(offset + dur, 2)
                if end_t <= video_duration:
                    clauses.append(f"between(t,{offset},{end_t})")
            if clauses:
                base += f":enable='{'+'.join(clauses)}'"

    return base

def validate_ffmpeg_command(cmd: str) -> bool:
    if not cmd or len(cmd) > 1000:
        return False
    if BAD_CHARS_RE.search(cmd):
        return False
    tokens = cmd.split()
    if len(tokens) > 48:
        return False
    for t in tokens:
        if t.startswith("-"):
            if t in FORBIDDEN_FLAGS:
                return False
            if t not in ALLOWED_FLAGS:
                return False
    return True

async def probe_video(file_path: str) -> dict:
    cmd = [
        config.FFPROBE_BIN, "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", file_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, _ = await proc.communicate()
    try:
        data = json.loads(out or b"{}")
    except json.JSONDecodeError:
        data = {}

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_codec = None
    audio_codecs = []
    subtitle_codecs = []
    width = height = 0
    fps = 0.0

    for s in streams:
        ct = s.get("codec_type")
        if ct == "video" and video_codec is None:
            video_codec = s.get("codec_name")
            width = int(s.get("width") or 0)
            height = int(s.get("height") or 0)
            fps_str = s.get("r_frame_rate") or "0/1"
            try:
                num, den = fps_str.split("/")
                den_f = float(den)
                fps = float(num) / den_f if den_f else 0.0
            except (ValueError, ZeroDivisionError):
                fps = 0.0
        elif ct == "audio":
            audio_codecs.append(s.get("codec_name"))
        elif ct == "subtitle":
            subtitle_codecs.append(s.get("codec_name"))

    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        file_size = int(fmt.get("size") or 0)
    except (TypeError, ValueError):
        file_size = 0

    return {
        "video_codec": video_codec,
        "audio_codecs": audio_codecs,
        "subtitle_codecs": subtitle_codecs,
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "file_size": file_size,
    }

def _copyable_subs(subtitle_codecs, out_fmt: str):
    result = []
    for sc in subtitle_codecs or []:
        sc_l = (sc or "").lower()
        if out_fmt == "mp4":
            if sc_l in ("subrip", "ass", "ssa", "mov_text"):
                result.append(sc_l)
        else:
            if sc_l not in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"):
                result.append(sc_l)
    return result

def generate_ffmpeg_cmd(input_path: str, output_path: str, settings: dict, probe_data: dict):
    video = settings.get("video", {})
    audio = settings.get("audio", {})
    wm = settings.get("watermark", {})
    metadata = settings.get("metadata", {})
    custom = settings.get("custom_ffmpeg", {}) or {}
    active_custom = settings.get("active_custom_ffmpeg", "")

    codec = video.get("codec", "libx264")
    crf = str(video.get("crf", "23"))
    preset = video.get("preset", "slow")
    resolutions = video.get("resolution") or ["720p"]
    if isinstance(resolutions, str):
        resolutions = [resolutions]
    sub_mode = video.get("subtitle_mode", "copy")
    out_fmt = video.get("output_format", "mkv")
    remux = bool(video.get("remux", False))

    src_w = int(probe_data.get("width") or 0)
    src_h = int(probe_data.get("height") or 0)
    src_fps = float(probe_data.get("fps") or 0)
    duration = float(probe_data.get("duration") or 0)

    cmd = [config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-stats", "-y"]

    trim_start = float(video.get("trim_start") or 0)
    trim_end = float(video.get("trim_end") or 0)
    if trim_start > 0:
        cmd += ["-ss", str(trim_start)]
    cmd += ["-i", input_path]
    if trim_start > 0 and trim_end > trim_start:
        cmd += ["-to", str(trim_end - trim_start)]

    sample_seconds = int(video.get("sample_seconds") or 0)
    if sample_seconds > 0:
        cmd += ["-t", str(sample_seconds)]

    cmd += ["-map", "0:v:0"]

    audio_track = audio.get("track", "all")
    if audio_track == "none":
        cmd += ["-an"]
    else:
        if audio_track in ("1", "2"):
            cmd += ["-map", f"0:a:{int(audio_track) - 1}?"]
        else:
            cmd += ["-map", "0:a?"]

    copy_subs = _copyable_subs(probe_data.get("subtitle_codecs"), out_fmt)
    if sub_mode == "copy" and copy_subs:
        cmd += ["-map", "0:s?"]

    if remux:
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", codec]
        threads = config.FFMPEG_THREADS
        if codec == "libx264":
            cmd += ["-crf", crf, "-preset", preset, "-threads", str(threads)]
        elif codec == "libx265":
            # libx265 uses x265-params for threading; the global -threads flag
            # has no effect on it and can cause conflicts.
            cmd += [
                "-crf", crf, "-preset", preset, "-tag:v", "hvc1",
                "-x265-params", f"pools={threads}:frame-threads={min(threads, 4)}",
            ]
        elif codec == "libvpx-vp9":
            # row-mt already enables multi-threading; -threads controls tile/row workers
            cmd += ["-crf", crf, "-b:v", "0", "-cpu-used", "2",
                    "-row-mt", "1", "-threads", str(threads)]
        elif codec == "libsvtav1":
            # SVT-AV1: CRF-based quality, numeric preset (0=slowest/best, 8=fastest)
            # preset comes from settings (default 4 = balanced)
            try:
                svt_preset = max(0, min(8, int(preset)))
            except (TypeError, ValueError):
                svt_preset = 4
            cmd += [
                "-crf", crf, "-b:v", "0",
                "-svtav1-params", f"preset={svt_preset}:lp={threads}",
            ]
        cmd += ["-pix_fmt", "yuv420p"]

        target_res = resolutions[0] if resolutions else "720p"
        tw, th = RES_MAP.get(target_res, ("1280", "720"))

        vf_parts = []
        if src_w and src_h:
            src_ratio = src_w / src_h
            target_ratio = int(tw) / int(th)
            if abs(src_ratio - target_ratio) > 0.05:
                vf_parts.append(f"scale=-2:{th}:flags=lanczos,setsar=1")
            else:
                vf_parts.append(f"scale={tw}:{th}:flags=lanczos,setsar=1")
        else:
            vf_parts.append(f"scale={tw}:{th}:flags=lanczos,setsar=1")

        if src_fps > 60:
            cmd += ["-r", "60"]

        if wm.get("enabled"):
            vf_parts.append(build_watermark_filter(wm, duration, int(tw), int(th)))

        cmd += ["-vf", ",".join(vf_parts)]

    if audio_track != "none":
        a_codec = audio.get("codec", "aac")
        src_audio = [(c or "").lower() for c in (probe_data.get("audio_codecs") or [])]
        problematic = {"dts", "truehd", "eac3", "atmos", "dts-hd", "mlp"}
        if any(c in problematic for c in src_audio) and a_codec == "copy":
            a_codec = "aac"

        if a_codec == "copy":
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", a_codec, "-b:a", audio.get("bitrate", "128k")]
            if a_codec == "aac":
                cmd += ["-ac", "2"]
            if audio.get("normalize"):
                cmd += ["-af", "loudnorm"]

    if sub_mode == "drop" or not copy_subs:
        cmd += ["-sn"]
    else:
        cmd += ["-c:s", "copy"]

    for k, v in (metadata.get("global") or {}).items():
        if v:
            cmd += ["-metadata", f"{k}={v}"]

    cmd += [
        "-max_muxing_queue_size", "2048",
        "-bufsize", "2M",
    ]

    if out_fmt == "mp4":
        cmd += ["-movflags", "+faststart"]

    if active_custom and active_custom in custom:
        custom_str = str(custom[active_custom]).strip()
        if validate_ffmpeg_command(custom_str):
            cmd += custom_str.split()

    cmd += [output_path]
    return cmd
