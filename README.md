# Encode Bot

A Pyrogram-based Telegram bot that encodes videos using FFmpeg.
Designed to run as 3 parallel Heroku worker dynos — one per quality.

---

## Supported Codecs

| Codec | Setting name | Notes |
|-------|-------------|-------|
| H.264 | `libx264` | Best compatibility |
| H.265 / HEVC | `libx265` | Good compression, HVC1 tagged |
| VP9 | `libvpx-vp9` | Open, good quality |
| AV1 | `libsvtav1` | Best compression, memory-optimised |

---

## SVT-AV1 Memory Optimisations (this fork)

Running SVT-AV1 on a Heroku 1 GB dyno requires care.
The following params are applied automatically in `bot/func/ffmpeg_utils.py`:

| Param | Value | Why |
|-------|-------|-----|
| `lookahead=0` | disabled | Kills the 120-frame buffer — biggest RAM saver |
| `tile-columns=0` | 0 | Single tile column, lowest RAM |
| `tile-rows=0` | 0 | Single tile row |
| `enable-qm=1` | on | Free quality gain, no extra RAM |
| `keyint=250` | 250 | Standard GOP for streaming |
| `lp` | 1 (1080p) / 2 (≤720p) | Logical processors capped per resolution |

### Estimated peak RAM after fixes

| Resolution | Peak RAM | Safe on 1 GB dyno? |
|------------|----------|-------------------|
| 480p | ~180 MB | ✅ Yes |
| 720p | ~350 MB | ✅ Yes |
| 1080p | ~700 MB | ⚠️ Borderline — use `FFMPEG_THREADS=1` |

### x265 fix also included

`lookahead-slices=0` added to x265-params — cuts peak RAM by ~30–50%
for 720p/1080p encodes.

---

## 3-Bot Heroku Setup (480p + 720p + 1080p)

Deploy the same code to 3 separate Heroku apps.
Set these config vars differently per app:

### encode01 — 480p
```
FFMPEG_THREADS=2
```
In bot settings: codec `libsvtav1`, preset `8`, resolution `480p`

### encode02 — 720p
```
FFMPEG_THREADS=2
```
In bot settings: codec `libsvtav1`, preset `8`, resolution `720p`

### encode03 — 1080p
```
FFMPEG_THREADS=1
```
In bot settings: codec `libsvtav1`, preset `8`, resolution `1080p`

> **Tip:** For 1080p, upgrade to a Heroku Performance-M dyno (2.5 GB RAM)
> if you want to use `lp=2` or a slower preset for better quality.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TG_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `APP_ID` | ✅ | Telegram API App ID |
| `API_HASH` | ✅ | Telegram API Hash |
| `LOG_CHANNEL` | ✅ | Telegram channel ID (negative integer) |
| `OWNER_ID` | ✅ | Your Telegram user ID |
| `DATABASE_URL` | ✅ | MongoDB connection string |
| `DATABASE_NAME` | ❌ | MongoDB database name (default: EncoderBot) |
| `FFMPEG_THREADS` | ❌ | FFmpeg thread count (default: 1, keep ≤2 on 1 GB) |
| `BOT_NAME` | ❌ | Display name |
| `PORT` | ❌ | Health server port (default: 8080) |

---

## Quality Presets

| Preset | Codec | CRF | Speed | Description |
|--------|-------|-----|-------|-------------|
| `fast` | H.264 | 23 | veryfast | Quick encode, larger file |
| `balanced` | H.264 | 22 | medium | Good balance |
| `compact` | H.265 | 28 | slow | Smallest HEVC file |
| `av1` | SVT-AV1 | 32 | 8 | Best compression (new) |

---

## Procfile

```
worker: python -m bot
```
