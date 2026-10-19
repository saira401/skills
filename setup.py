#!/usr/bin/env python3
"""Check Python version, create .env with a random admin password (printed once), create data/."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ required (found {sys.version.split()[0]})")
    from server.config import ensure_env
    pw = ensure_env()
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    if pw:
        print(f"Created .env\nAdmin username: admin\nAdmin password: {pw}  (shown once - it is stored in .env)")
    else:
        print(".env already exists - nothing changed")
    print("Setup OK. Start the server with:  python main.py")


if __name__ == "__main__":
    main()
