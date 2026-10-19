"""Entry point: config, DB, seed, listen, background sweep."""
import sys
import threading
import time

from . import db, seed
from .app import Handler, Server
from .config import cfg, ensure_env, ROOT
from .lib import qr, sessions
from .services import attempts, live


def sweep_loop():
    n = 0
    while True:
        time.sleep(1)
        try:
            attempts.sweep()
            live.tick()
            n += 1
            if n % 600 == 0:
                sessions.purge_expired()
        except Exception as e:  # keep the loop alive
            print("sweep error:", e, file=sys.stderr)


def main():
    if sys.version_info < (3, 10):
        sys.exit("ExamShield needs Python 3.10+")
    pw = ensure_env()
    cfg.load()
    if not cfg.ADMIN_PASSWORD:
        import secrets
        cfg.ADMIN_PASSWORD, pw = secrets.token_urlsafe(12), None
        print(f"\n  ADMIN_PASSWORD is blank in .env - using a one-time password for this first run: {cfg.ADMIN_PASSWORD}")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    db.init()
    seeded = seed.run_seed()
    if pw or seeded:
        print("\n  First run: admin login  ->  username: admin   password: " + (pw or cfg.ADMIN_PASSWORD))
        print("  (printed once; it is stored in .env)")
    threading.Thread(target=sweep_loop, daemon=True).start()
    server = Server((cfg.HOST, cfg.PORT), Handler)
    base = cfg.base_url()
    join = f"{base}/?code=DEMO01"
    print(f"\n  ExamShield running\n  Student join : {join}\n  Examiner     : {base}/admin\n")
    print(qr.terminal(qr.make(join)))
    print("\n  Scan the QR to join demo exam DEMO01. Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
        server.server_close()
