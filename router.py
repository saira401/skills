"""Path-param router + response helpers."""
import json
import re


class HttpError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


class Response:
    def __init__(self, body=b"", status=200, ctype="application/json", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body, self.status, self.ctype = body, status, ctype
        self.headers = list(headers or [])
        self.cookies = []

    @staticmethod
    def json(obj, status=200):
        return Response(json.dumps(obj, separators=(",", ":")), status)


class Stream:
    """Server-Sent Events (or any streamed body): iterator of bytes."""
    def __init__(self, iterator, ctype="text/event-stream"):
        self.iterator, self.ctype, self.status = iterator, ctype, 200


class Router:
    def __init__(self):
        self.routes = []

    @staticmethod
    def _compile(pattern):
        rx = re.sub(r"<int:(\w+)>", r"(?P<\1>\\d+)", pattern)
        rx = re.sub(r"(?<!\?P)<(\w+)>", r"(?P<\1>[^/]+)", rx)
        return re.compile(rx)

    def add(self, method, pattern, handler, role=None):
        self.routes.append((method, self._compile(pattern), handler, role))

    def _deco(self, method):
        def outer(pattern, role=None):
            def deco(fn):
                self.add(method, pattern, fn, role)
                return fn
            return deco
        return outer

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete"):
            return self._deco(name.upper())
        raise AttributeError(name)

    def dispatch(self, req):
        path_matched = False
        for method, rx, handler, role in self.routes:
            m = rx.fullmatch(req.path)
            if not m:
                continue
            path_matched = True
            if method != req.method:
                continue
            if role and (not req.session or req.session["role"] != role):
                raise HttpError(401, "Authentication required")
            req.params = {k: int(v) if v.isdigit() else v for k, v in m.groupdict().items()}
            return handler(req)
        raise HttpError(405 if path_matched else 404, "Method not allowed" if path_matched else "Not found")


R = Router()
