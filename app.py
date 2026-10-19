"""HTTP pipeline: headers -> static -> rate limit -> session -> CSRF -> router."""
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

from . import routes  # noqa: F401  (registers routes)
from .config import ROOT, cfg
from .lib import sessions
from .lib.router import R, HttpError, Response, Stream
from .lib.security import SEC_HEADERS, RateLimiter, csrf_check

CLIENT = ROOT / "client"
PAGES = {"/": "index.html", "/admin": "admin.html", "/exam": "exam.html"}
STATIC_EXT = {".html", ".css", ".js", ".svg", ".ico", ".png"}
UPLOAD_PATH = re.compile(r"^/api/admin/exams/\d+/notes$")
JSON_LIMIT = 256 * 1024


class Request:
    def __init__(self, handler, method, split):
        self.method, self.path = method, split.path
        self.query = dict(parse_qsl(split.query))
        self.headers = handler.headers
        self.ip = handler.client_address[0]
        self.body, self.session, self.params = b"", None, {}
        self.cookie_token = None
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == "es_session":
                    self.cookie_token = v

    def json(self):
        try:
            d = json.loads(self.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "Invalid JSON")
        if not isinstance(d, dict):
            raise HttpError(400, "JSON object expected")
        return d


class App:
    def __init__(self):
        self.limiter = RateLimiter()

    def static(self, path):
        name = PAGES.get(path)
        if name is None:
            f = (CLIENT / path.lstrip("/")).resolve()
            try:
                f.relative_to(CLIENT)
            except ValueError:
                return None
            if f.suffix not in STATIC_EXT or not f.is_file():
                return None
        else:
            f = CLIENT / name
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        return Response(f.read_bytes(), 200, ctype, [("Cache-Control", "no-cache")])

    def handle(self, req):
        if req.method == "GET" and not req.path.startswith("/api/"):
            r = self.static(req.path)
            if r:
                return r
            raise HttpError(404, "Not found")
        req.session = sessions.get(req.cookie_token)
        login = req.path in ("/api/auth/admin/login", "/api/student/join")
        key = f"login:{req.ip}" if login else f"api:{req.ip}:{(req.session or {}).get('token_hash', '')}"
        if not self.limiter.allow(key, cfg.RATE_LIMIT_LOGIN if login else cfg.RATE_LIMIT_API):
            raise HttpError(429, "Too many requests - slow down")
        csrf_check(req)
        out = R.dispatch(req)
        if isinstance(out, (Response, Stream)):
            return out
        return Response.json(out)


APP = App()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ExamShield"

    def log_message(self, fmt, *args):
        if not cfg.QUIET:
            super().log_message(fmt, *args)

    def _run(self):
        split = urlsplit(self.path)
        req = Request(self, self.command, split)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            limit = cfg.MAX_UPLOAD_MB * 1024 * 1024 if UPLOAD_PATH.match(split.path) and self.command == "POST" else JSON_LIMIT
            if n > limit:
                self.close_connection = True
                raise HttpError(413, "Request body too large")
            req.body = self.rfile.read(n) if n else b""
            resp = APP.handle(req)
        except HttpError as e:
            resp = Response.json({"error": e.message, **e.extra}, e.status)
        except Exception as e:  # never leak internals
            self.log_error("unhandled: %r", e)
            import traceback
            traceback.print_exc()
            resp = Response.json({"error": "Internal server error"}, 500)
        self._send(resp)

    def _send(self, resp):
        self.send_response(resp.status)
        for k, v in SEC_HEADERS:
            self.send_header(k, v)
        self.send_header("Content-Type", resp.ctype)
        if isinstance(resp, Stream):
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            try:
                for chunk in resp.iterator:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                resp.iterator.close()
            return
        self.send_header("Content-Length", str(len(resp.body)))
        if not any(k.lower() == "cache-control" for k, _ in resp.headers):
            self.send_header("Cache-Control", "no-store")
        for k, v in resp.headers:
            self.send_header(k, v)
        for c in resp.cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(resp.body)

    do_GET = do_POST = do_PUT = do_DELETE = _run


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
