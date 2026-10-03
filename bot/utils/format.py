def humanbytes(size) -> str:
    if size is None:
        return "0 B"
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "0 B"
    if size < 1024:
        return f"{int(size)} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        size /= 1024.0
        if size < 1024:
            return f"{size:.2f} {unit}"
    return f"{size:.2f} PB"

def TimeFormatter(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "0s"
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)
