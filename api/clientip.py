"""Real client IP behind DigitalOcean App Platform.

App Platform does NOT put the client's address in X-Forwarded-For: that
header carries the IP of the DO ingress server that forwarded the request.
The client's real address is in the ``do-connecting-ip`` header
(https://docs.digitalocean.com/products/app-platform/support/).

Keying the auth rate limiter on X-Forwarded-For therefore lumped every
user of the site into ONE bucket (the ingress IP) — 10 auth requests per
minute shared across everybody, so logins failed with 429 "Too many
attempts" as soon as a handful of people used the app.

Resolution order:
  1. do-connecting-ip   (DigitalOcean App Platform)
  2. cf-connecting-ip   (Cloudflare in front of the app)
  3. x-real-ip          (nginx-style single proxy)
  4. last X-Forwarded-For entry (the one appended by our own proxy)
  5. the TCP peer address
"""

_CANDIDATES = (b"do-connecting-ip", b"cf-connecting-ip", b"x-real-ip")


def client_ip_from_headers(headers, peer):
    """``headers`` is a mapping of lower-case *bytes* names -> bytes values
    (the ASGI scope shape). ``peer`` is the TCP peer host or None."""
    for name in _CANDIDATES:
        v = headers.get(name)
        if v:
            v = v.decode("latin-1").strip()
            if v:
                return v
    fwd = headers.get(b"x-forwarded-for", b"").decode("latin-1")
    if fwd.strip():
        return fwd.split(",")[-1].strip()
    return peer or "unknown"


def client_ip_from_request(request):
    """Starlette Request variant (headers are str-keyed and case-insensitive)."""
    headers = {k.lower().encode("latin-1"): v.encode("latin-1")
               for k, v in request.headers.items()}
    peer = request.client.host if request.client else None
    return client_ip_from_headers(headers, peer)
