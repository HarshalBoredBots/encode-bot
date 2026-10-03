from aiohttp import web
from bot.logger import get_logger

log = get_logger(__name__)

async def health_handler(request):
    return web.Response(text="OK")

async def start_server(port: int):
    app = web.Application()
    app.router.add_get("/", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health server listening on port %s", port)
    return runner
