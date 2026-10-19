"""SQLite-backed sessions. Cookie holds a random 256-bit token; DB holds only its SHA-256."""
import hashlib
import secrets
import time

from .. import db
from ..config import cfg


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create(role, user_id, exam_id=None, ip=""):
    token = secrets.token_urlsafe(32)  # 256 bits
    now = int(time.time())
    db.run("INSERT INTO sessions(token_hash, role, user_id, exam_id, ip, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
           (hash_token(token), role, user_id, exam_id, ip, now, now + cfg.SESSION_TTL_H * 3600))
    return token


def get(token):
    if not token:
        return None
    row = db.q("SELECT * FROM sessions WHERE token_hash=?", (hash_token(token),), one=True)
    if row and row["expires_at"] < time.time():
        destroy(token)
        return None
    return row


def destroy(token):
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", (hash_token(token),))


def purge_expired():
    db.run("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
