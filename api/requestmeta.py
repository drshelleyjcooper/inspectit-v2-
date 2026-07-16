"""F12: request context (client IP, user agent) made available to any code in
the request path — notably db.audit() — without threading it through every
endpoint signature. Uses a ContextVar, which anyio propagates into the
threadpool where sync endpoints run.
"""
import contextvars

request_meta = contextvars.ContextVar("request_meta", default=None)


class RequestMetaMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        fwd = headers.get(b"x-forwarded-for", b"").decode("latin-1")
        if fwd:
            # Last entry = appended by our own proxy; earlier ones are
            # client-supplied (same rule as ratelimit.client_ip).
            ip = fwd.split(",")[-1].strip()
        else:
            client = scope.get("client")
            ip = client[0] if client else None
        ua = headers.get(b"user-agent", b"").decode("latin-1")[:300] or None
        token = request_meta.set({"ip": ip, "user_agent": ua})
        try:
            await self.app(scope, receive, send)
        finally:
            request_meta.reset(token)
