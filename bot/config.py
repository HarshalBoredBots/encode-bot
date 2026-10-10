import os
import shutil
from dotenv import load_dotenv

load_dotenv()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
APP_ID = int(os.environ.get("APP_ID") or 0)
API_HASH = os.environ.get("API_HASH", "")
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL") or 0)
OWNER_ID = int(os.environ.get("OWNER_ID") or 0)
PORT = int(os.environ.get("PORT", "8080"))
BOT_NAME = os.environ.get("BOT_NAME", "Encoder Bot")
BOT_VERSION = os.environ.get("BOT_VERSION", "1.0.0")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "EncoderBot")

MAX_CONCURRENT_JOBS = 1
MAX_CONCURRENT_DOWNLOADS = 1
MAX_CONCURRENT_UPLOADS = 1
MAX_CONCURRENT_JOBS_PER_USER = 1      # only 1 encodes at a time
MAX_QUEUED_JOBS_PER_USER = 25         # up to 25 files can wait in queue per user
MAX_QUEUE_LENGTH = 50
TG_BOT_WORKERS = 4
MAX_CONCURRENT_TRANSMISSIONS = 4

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
MAX_OUTPUT_SIZE = 4 * 1024 * 1024 * 1024
MIN_FREE_DISK_BYTES = 300 * 1024 * 1024  # 300 MB (safe for Heroku Standard 2X)

DOWNLOAD_DIR = "downloads"
THUMB_DIR = "thumbs"
WATERMARK_DIR = "watermarks"
FONT_DIR = "fonts"
FONT_PATH = "bot/fonts/Kufam-SemiBold.ttf"

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "/app/ffmpeg-static/ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "/app/ffmpeg-static/ffprobe")

# On Heroku Standard-2X (2 vCPU), use both cores for FFmpeg.
# The bot itself is I/O-bound (asyncio) and consumes negligible CPU during encoding,
# so we don't need to reserve a core for it.
# Override by setting FFMPEG_THREADS in your Heroku config vars if needed.
# Heroku reports 8 CPUs but RAM is only 1GB
# Cap threads at 2 to prevent OOM crashes (especially with SVT-AV1)
_cpu_count = min(os.cpu_count() or 2, 2)
FFMPEG_THREADS = int(os.environ.get("FFMPEG_THREADS", str(_cpu_count)))

FFMPEG_WALL_TIMEOUT = 28800

UI_UPDATE_INTERVAL = 4.0
PROGRESS_CALLBACK_INTERVAL = 4.0

ADMIN_IDS = {OWNER_ID} if OWNER_ID else set()

def validate_config():
    errors = []
    if not TG_BOT_TOKEN or ":" not in TG_BOT_TOKEN:
        errors.append("TG_BOT_TOKEN is missing or invalid.")
    if not APP_ID:
        errors.append("APP_ID is missing.")
    if not API_HASH or API_HASH in ("your_api_hash", "changeme"):
        errors.append("API_HASH is missing or is a placeholder.")
    if not LOG_CHANNEL or LOG_CHANNEL >= 0:
        errors.append("LOG_CHANNEL must be a negative integer (channel/group id).")
    if not OWNER_ID:
        errors.append("OWNER_ID is missing.")
    if not DATABASE_URL:
        errors.append("DATABASE_URL is missing.")
    if not shutil.which(FFMPEG_BIN) and not os.path.isfile(FFMPEG_BIN):
        errors.append(f"ffmpeg binary not found: {FFMPEG_BIN}")
    if not shutil.which(FFPROBE_BIN) and not os.path.isfile(FFPROBE_BIN):
        errors.append(f"ffprobe binary not found: {FFPROBE_BIN}")

    for d in (DOWNLOAD_DIR, THUMB_DIR, WATERMARK_DIR, FONT_DIR, "logs", "bot/fonts"):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            errors.append(f"cannot create directory {d}: {e}")

    if errors:
        raise RuntimeError("Configuration errors:\n - " + "\n - ".join(errors))
    return True
