"""Configuration: .env file + environment variables, LAN IP auto-detect."""
import os
import secrets
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "PORT": "8080", "HOST": "0.0.0.0", "BASE_URL": "", "ADMIN_PASSWORD": "",
    "DB_PATH": "./data/examshield.db", "RATE_LIMIT_API": "600", "RATE_LIMIT_LOGIN": "30",
    "FAST_ANSWER_MS": "800", "MAX_STRIKES": "3", "MAX_UPLOAD_MB": "5",
    "SESSION_TTL_H": "12", "PBKDF2_ITER": "200000",
    # Parent e-mail delivery (optional). Without SMTP_HOST, mails are saved as .eml in data/outbox/
    "SMTP_HOST": "", "SMTP_PORT": "587", "SMTP_USER": "", "SMTP_PASSWORD": "",
    "SMTP_FROM": "", "SMTP_TLS": "1", "MAIL_SYNC": "0", "QUIET": "0",
}
INT_KEYS = {"PORT", "RATE_LIMIT_API", "RATE_LIMIT_LOGIN", "FAST_ANSWER_MS", "MAX_STRIKES",
            "MAX_UPLOAD_MB", "SESSION_TTL_H", "PBKDF2_ITER", "SMTP_PORT"}
BOOL_KEYS = {"SMTP_TLS", "MAIL_SYNC", "QUIET"}

ENV_TEMPLATE = """PORT=8080
HOST=0.0.0.0
BASE_URL=           # auto-detected if blank
ADMIN_PASSWORD={pw}     # generated on first run
DB_PATH=./data/examshield.db
RATE_LIMIT_API=600  # requests/min
RATE_LIMIT_LOGIN=30
FAST_ANSWER_MS=800
MAX_STRIKES=3
MAX_UPLOAD_MB=5     # per notes file
# --- optional: e-mail results to parents. Leave SMTP_HOST blank to save .eml files in data/outbox/
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_TLS=1
"""


def parse_env_file(path):
    vals = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.split("#")[0].strip()
    return vals


def ensure_env(path=None):
    """Create .env on first run. Returns the generated admin password (or None if .env existed)."""
    p = Path(path or ROOT / ".env")
    if p.exists():
        return None
    pw = secrets.token_urlsafe(12)
    p.write_text(ENV_TEMPLATE.format(pw=pw), encoding="utf-8")
    return pw


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Config:
    def __init__(self):
        self.load()

    def load(self, env_path=None, overrides=None):
        vals = dict(DEFAULTS)
        vals.update(parse_env_file(env_path or ROOT / ".env"))
        for k in DEFAULTS:
            if k in os.environ:
                vals[k] = os.environ[k]
        vals.update({k: str(v) for k, v in (overrides or {}).items()})
        for k, v in vals.items():
            if k in INT_KEYS:
                v = int(v)
            elif k in BOOL_KEYS:
                v = str(v).lower() in ("1", "true", "yes")
            setattr(self, k, v)
        dbp = Path(self.DB_PATH)
        self.DB_PATH = str(dbp if dbp.is_absolute() else (ROOT / dbp).resolve())

    @property
    def data_dir(self):
        return Path(self.DB_PATH).parent

    @property
    def upload_dir(self):
        return self.data_dir / "uploads"

    @property
    def outbox_dir(self):
        return self.data_dir / "outbox"

    def base_url(self):
        return self.BASE_URL.rstrip("/") if self.BASE_URL else f"http://{lan_ip()}:{self.PORT}"


cfg = Config()
