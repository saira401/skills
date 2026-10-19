"""CSP/security headers, CSRF check, rate limiter."""
import threading
import time
from urllib.parse import urlparse

from .router import HttpError

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SEC_HEADERS = [
    ("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
]


def csrf_check(req):
    """SameSite cookie + custom header + Origin/Host check for every state-changing request."""
    if req.method in ("GET", "HEAD", "OPTIONS"):
        return
    if req.headers.get("X-Requested-With") != "ExamShield":
        raise HttpError(403, "Missing X-Requested-With header")
    origin = req.headers.get("Origin")
    if origin and origin != "null":
        if urlparse(origin).netloc != req.headers.get("Host", ""):
            raise HttpError(403, "Cross-origin request blocked")
    elif origin == "null":
        raise HttpError(403, "Cross-origin request blocked")


class RateLimiter:
    def __init__(self):
        self.hits, self.lock, self._n = {}, threading.Lock(), 0

    def allow(self, key, limit, window=60.0):
        now = time.monotonic()
        with self.lock:
            self._n += 1
            if self._n % 500 == 0:
                self.hits = {k: [t for t in v if now - t < window] for k, v in self.hits.items()}
                self.hits = {k: v for k, v in self.hits.items() if v}
            lst = [t for t in self.hits.get(key, []) if now - t < window]
            if len(lst) >= limit:
                self.hits[key] = lst
                return False
            lst.append(now)
            self.hits[key] = lst
            return True
