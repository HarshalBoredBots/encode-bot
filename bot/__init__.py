from pyrogram import Client
from bot import config

app = Client(
    "EncoderBot",
    api_id=config.APP_ID,
    api_hash=config.API_HASH,
    bot_token=config.TG_BOT_TOKEN,
    workers=config.TG_BOT_WORKERS,
    plugins=dict(root="plugins"),
)
