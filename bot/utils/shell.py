import asyncio
from bot.logger import get_logger

log = get_logger(__name__)

async def run_shell(cmd: str, timeout: int = 120):
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (out or b"").decode(errors="ignore")
    except asyncio.TimeoutError:
        return "Command timed out."
    except Exception as e:
        return f"Shell error: {e}"
