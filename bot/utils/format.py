import os
import re


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


# ── Auto-encode template resolver ────────────────────────────────────────────

# Numbers that are NOT episode numbers (quality indicators, years, etc.)
_SKIP_NUMS = {360, 480, 720, 1080, 1440, 2160, 2020, 2021, 2022, 2023, 2024, 2025}


def _extract_episode(text: str):
    """Return the episode number (int) extracted from *text*, or None."""
    # Priority 1 – explicit SxxEyy markers
    for pat in [
        re.compile(r'S\d+E(\d+)',              re.IGNORECASE),
        re.compile(r'S\d+\s+E(\d+)',           re.IGNORECASE),
        re.compile(r'S\d+[._-]E(\d+)',         re.IGNORECASE),
        re.compile(r'S\d+\s*-\s*E(\d+)',       re.IGNORECASE),
    ]:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            if n not in _SKIP_NUMS:
                return n

    # Priority 2 – bare number then E (01E02 style)
    m = re.search(r'(?<!\d)\d+E(\d+)(?!\d)', text, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if n not in _SKIP_NUMS:
            return n

    # Priority 3 – Episode / EP keyword
    for pat in [
        re.compile(r'\bEpisode\s*(\d+)', re.IGNORECASE),
        re.compile(r'\bEP\s*(\d+)\b',   re.IGNORECASE),
    ]:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            if n not in _SKIP_NUMS:
                return n

    # Priority 4 – standalone E12 / [E12]
    for pat in [
        re.compile(r'(?<![A-Za-z\d\[\(])E(\d+)(?!\d)', re.IGNORECASE),
        re.compile(r'[\[\(]E(\d+)[\]\)]',               re.IGNORECASE),
    ]:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            if n not in _SKIP_NUMS:
                return n

    # Priority 5 – bare standalone number (last resort)
    for m in re.finditer(r'(?:^|[^0-9A-Za-z])(\d{1,4})(?:[^0-9A-Za-z]|$)', text):
        n = int(m.group(1))
        if 1 <= n <= 9999 and n not in _SKIP_NUMS:
            return n

    return None


def _extract_season(text: str):
    """Return the season number (int) extracted from *text*, or None."""
    for pat in [
        re.compile(r'S(\d+)[._-]?E\d+',                    re.IGNORECASE),
        re.compile(r'(?:Season|SEASON|season)[\s._-]*(\d+)', re.IGNORECASE),
        re.compile(r'\bS(\d+)\b(?!E\d)',                    re.IGNORECASE),
        re.compile(r'[\[\(]S(\d+)[\]\)]',                  re.IGNORECASE),
    ]:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 99:
                return n
    return None


def _extract_audio(text: str):
    """Return audio label (e.g. 'Dual', 'Hindi') from *text*, or None."""
    keywords = {
        'Dual':    r'Dual(?:audio)?',
        'Multi':   r'Multi(?:audio)?',
        'Hindi':   r'Hindi',
        'English': r'English',
        'Telugu':  r'Telugu',
        'Tamil':   r'Tamil',
        'Jap':     r'Jap',
    }
    found = [label for label, pat in keywords.items()
             if re.search(pat, text, re.IGNORECASE)]
    return ' '.join(found) if found else None


def resolve_encode_template(template: str, source_filename: str, encode_resolution: str = None) -> str:
    """
    Resolve an auto-encode filename template against the source filename.

    Supported placeholders (case-insensitive):
        {episode}  – episode number zero-padded to 2 digits (e.g. 05)
        {season}   – season number (e.g. 1)
        {quality}  – ALWAYS uses encode_resolution (e.g. 480p, 720p, 1080p)
                     if provided; falls back to extraction from source filename
        {audio}    – audio label (e.g. Dual, Hindi)

    If a placeholder cannot be resolved the placeholder text is left
    **empty** and any surrounding empty brackets ([], (), {}) are stripped.

    The file extension is taken from the template if it has one; it is
    overridden by encode_resolution's implied format only when the template
    extension differs from settings["video"]["output_format"] — that logic
    lives in the caller.
    """
    # Strip CRC32 hashes (8-hex in brackets) before extraction
    clean_src = re.sub(r'\[[0-9A-Fa-f]{8}\]', '', source_filename).strip()

    ep  = _extract_episode(clean_src)
    s   = _extract_season(clean_src)
    aud = _extract_audio(clean_src)

    ep_str  = str(ep).zfill(2) if ep  is not None else ""
    s_str   = str(s)           if s   is not None else ""
    aud_str = aud or ""

    # {quality} always comes from the actual encode resolution when provided
    if encode_resolution:
        q_str = encode_resolution
    else:
        from bot.utils.format import _extract_quality  # avoid circular at module level
        q = _extract_quality(clean_src)
        q_str = q or ""

    result = template
    result = re.sub(r'\{episode\}', ep_str,  result, flags=re.IGNORECASE)
    result = re.sub(r'\{season\}',  s_str,   result, flags=re.IGNORECASE)
    result = re.sub(r'\{quality\}', q_str,   result, flags=re.IGNORECASE)
    result = re.sub(r'\{audio\}',   aud_str, result, flags=re.IGNORECASE)

    # Remove empty bracket pairs that result from missing placeholders
    result = re.sub(r'\[\s*\]', '', result)
    result = re.sub(r'\(\s*\)', '', result)
    result = re.sub(r'\{\s*\}', '', result)
    # Collapse multiple spaces
    result = re.sub(r'  +', ' ', result).strip()

    # Ensure the result has a file extension
    _, tmpl_ext = os.path.splitext(template)
    _, src_ext  = os.path.splitext(source_filename)
    if not os.path.splitext(result)[1]:
        result += (tmpl_ext or src_ext)

    return result


def _extract_quality(text: str):
    """Return quality string (e.g. '1080p', 'HEVC') from *text*, or None."""
    for pat in [
        re.compile(r'\b(4K|2K|2160p|1440p|1080p|720p|480p|360p)\b', re.IGNORECASE),
        re.compile(r'\b(HD(?:RIP)?|WEB(?:-)?DL|BLURAY)\b',           re.IGNORECASE),
        re.compile(r'\b(X264|X265|HEVC)\b',                           re.IGNORECASE),
    ]:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None
