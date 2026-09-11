"""Per-IP rate limiting for the auth endpoints (F3).

In-process sliding window — sufficient for a single API instance, which is
the deployment shape for the foreseeable future. If the app ever scales to
multiple instances, each instance enforces the limit independently (still a
meaningful brake on brute force); a shared store can replace this then.
"""
import logging
import time
from collections import deque

from fastapi import HTTPException, Request

from . import config
from .clientip import client_ip_from_request

log = logging.getLogger("inspectit")


class RateLimiter:
    def __init__(self, max_requests: int, window_s: int):
        self.max_requests = max_requests
        self.window_s = window_s
        self._hits = {}   # key -> deque[timestamps]

    def check(self, key: str):
        """Record a hit; raise 429 when the key exceeds the window budget."""
        now = time.monotonic()
        q = self._hits.get(key)
        if q is None:
            q = self._hits[key] = deque()
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.max_requests:
            retry = max(1, int(q[0] + self.window_s - now) + 1)
            log.warning("auth rate limit hit for %s (%d/%ds)", key,
                        self.max_requests, self.window_s)
            raise HTTPException(
                429, "Too many attempts — please wait a moment and try again.",
                headers={"Retry-After": str(retry)})
        q.append(now)
        # Opportunistic cleanup so idle keys don't accumulate forever: drop
        # every key whose newest hit is already outside the window.
        if len(self._hits) > 10000:
            stale = [k for k, v in self._hits.items() if not v or v[-1] < cutoff]
            for k in stale:
                del self._hits[k]

    def reset(self):
        self._hits.clear()


auth_limiter = RateLimiter(config.AUTH_RATE_LIMIT, config.AUTH_RATE_WINDOW_S)


def client_ip(request: Request) -> str:
    """Real client IP. On DO App Platform this is the do-connecting-ip
    header (X-Forwarded-For there holds the *ingress* IP, which is the same
    for every visitor). See api/clientip.py for the full resolution order."""
    return client_ip_from_request(request)


def rate_limit_auth(request: Request):
    auth_limiter.check(client_ip(request))
