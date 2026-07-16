"""F13: structured (JSON-lines) request logging to stdout, which App Platform
collects. One line per request: method, path, status, duration, client IP.
/health is skipped — platform probes would drown everything else.

Production run command should add --no-access-log so uvicorn's unstructured
access log doesn't duplicate these lines.
"""
import json
import logging
import time

logger = logging.getLogger("inspectit.access")


class AccessLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        start = time.monotonic()
        status = {"code": 0}

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            headers = dict(scope.get("headers") or [])
            fwd = headers.get(b"x-forwarded-for", b"").decode("latin-1")
            client = scope.get("client")
            ip = (fwd.split(",")[-1].strip() if fwd
                  else (client[0] if client else None))
            logger.info(json.dumps({
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status": status["code"],
                "ms": round((time.monotonic() - start) * 1000, 1),
                "ip": ip,
            }))
