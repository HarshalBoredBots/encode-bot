import os
import logging
import sys

os.makedirs("logs", exist_ok=True)

LOG_FILE = "logs/bot.log"
_fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")

_root = logging.getLogger()
_root.setLevel(logging.INFO)
if not _root.handlers:
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(_fmt)
    _root.addHandler(sh)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(_fmt)
    _root.addHandler(fh)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
