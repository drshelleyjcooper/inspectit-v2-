"""F4: global request-body size limit (default 75 MB, MAX_BODY_MB env).

Pure ASGI middleware. Two rules:
- Content-Length above the limit -> 413 before any body is read.
- A body with no Content-Length (chunked transfer) -> 411 Length Required;
  every normal client (browsers, httpx, the app's fetch calls) sends a
  length, and refusing chunked closes the only bypass around the check.

Note: the collections sync endpoint keeps its own stricter 25 MB per-key cap;
this limit mainly governs the one-time backup import.
"""
from starlette.responses import JSONResponse

METHODS_WITH_BODY = {"POST", "PUT", "PATCH"}


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in METHODS_WITH_BODY:
            headers = dict(scope.get("headers") or [])
            length = headers.get(b"content-length")
            if length is not None:
                try:
                    n = int(length)
                except ValueError:
                    n = None
                if n is not None and n > self.max_bytes:
                    resp = JSONResponse(
                        {"detail": "Request body too large "
                                   f"(limit {self.max_bytes // (1024*1024)} MB)"},
                        status_code=413)
                    await resp(scope, receive, send)
                    return
            elif b"chunked" in headers.get(b"transfer-encoding", b"").lower():
                resp = JSONResponse(
                    {"detail": "Content-Length required"}, status_code=411)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
