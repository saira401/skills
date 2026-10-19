"""Login / logout / whoami for examiners. (Student join lives in routes/student.py.)"""
import hashlib
import hmac
import secrets
import time

from .. import db
from ..config import cfg
from ..lib import sessions, validate as V
from ..lib.router import R, HttpError, Response

COOKIE = "es_session"


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), cfg.PBKDF2_ITER).hex()
    return h, salt


def verify_password(password, pw_hash, salt):
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, pw_hash)


def create_admin(username, password):
    h, salt = hash_password(password)
    db.run("INSERT INTO admins(username, pw_hash, salt, created_at) VALUES (?,?,?,?)", (username, h, salt, int(time.time())))


def set_session_cookie(resp, token):
    secure = "; Secure" if cfg.BASE_URL.startswith("https") else ""
    resp.cookies.append(f"{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={cfg.SESSION_TTL_H * 3600}{secure}")


def clear_cookie(resp):
    resp.cookies.append(f"{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")


@R.post("/api/auth/admin/login")
def admin_login(req):
    d = req.json()
    user = V.str_(d.get("username"), "username", 1, 60)
    pw = V.str_(d.get("password"), "password", 1, 200)
    row = db.q("SELECT * FROM admins WHERE username=?", (user,), one=True)
    ok = verify_password(pw, row["pw_hash"], row["salt"]) if row else verify_password(pw, "0" * 64, "dummy") and False
    if not row or not ok:
        raise HttpError(401, "Invalid username or password")
    if req.session:  # regenerate: never reuse a pre-login token
        sessions.destroy(req.cookie_token)
    resp = Response.json({"ok": True, "username": row["username"]})
    set_session_cookie(resp, sessions.create("admin", row["id"], None, req.ip))
    return resp


@R.post("/api/auth/logout")
def logout(req):
    sessions.destroy(req.cookie_token)
    resp = Response.json({"ok": True})
    clear_cookie(resp)
    return resp


@R.get("/api/auth/me")
def me(req):
    s = req.session
    if not s:
        return {"role": None}
    if s["role"] == "admin":
        a = db.q("SELECT username FROM admins WHERE id=?", (s["user_id"],), one=True)
        return {"role": "admin", "username": a["username"] if a else None}
    return {"role": "student"}
