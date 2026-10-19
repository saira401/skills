"""Study-notes upload/download for an exam topic. Files are validated, renamed and stored outside the web root."""
import os
import re
import secrets
import time

from .. import db
from ..config import cfg
from ..lib.router import HttpError

ALLOWED = {
    "pdf": "application/pdf", "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
MAGIC = {"pdf": [b"%PDF"], "png": [b"\x89PNG\r\n\x1a\n"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
         "docx": [b"PK\x03\x04"], "pptx": [b"PK\x03\x04"]}


def clean_filename(name):
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ]", "_", name, flags=re.UNICODE)[:100].strip(". ")
    if not name or "." not in name:
        raise HttpError(400, "A file name with an extension is required")
    return name


def validate(filename, data):
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED:
        raise HttpError(400, "File type not allowed. Allowed: " + ", ".join(sorted(ALLOWED)))
    if not data:
        raise HttpError(400, "Empty file")
    if len(data) > cfg.MAX_UPLOAD_MB * 1024 * 1024:
        raise HttpError(413, f"File too large (max {cfg.MAX_UPLOAD_MB} MB)")
    if ext in MAGIC and not any(data.startswith(m) for m in MAGIC[ext]):
        raise HttpError(400, f"File content does not look like a .{ext} file")
    if ext in ("txt", "md", "csv"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise HttpError(400, "Text notes must be UTF-8")
    return ext


def save(exam_id, filename, topic, data):
    filename = clean_filename(filename)
    ext = validate(filename, data)
    stored = secrets.token_hex(16) + "." + ext
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    (cfg.upload_dir / stored).write_bytes(data)
    cur = db.run("INSERT INTO exam_notes(exam_id, topic, filename, stored_name, mime, size, uploaded_at) VALUES (?,?,?,?,?,?,?)",
                 (exam_id, topic[:120], filename, stored, ALLOWED[ext], len(data), int(time.time())))
    return cur.lastrowid


def list_for(exam_id):
    return db.q("SELECT id, topic, filename, mime, size, uploaded_at FROM exam_notes WHERE exam_id=? ORDER BY id", (exam_id,))


def read(note_id, exam_id=None):
    n = db.q("SELECT * FROM exam_notes WHERE id=?", (note_id,), one=True)
    if not n or (exam_id is not None and n["exam_id"] != exam_id):
        raise HttpError(404, "Note not found")
    path = cfg.upload_dir / n["stored_name"]
    if not path.is_file():
        raise HttpError(404, "File missing")
    return n, path.read_bytes()


def delete(note_id):
    n = db.q("SELECT * FROM exam_notes WHERE id=?", (note_id,), one=True)
    if not n:
        raise HttpError(404, "Note not found")
    db.run("DELETE FROM exam_notes WHERE id=?", (note_id,))
    try:
        (cfg.upload_dir / n["stored_name"]).unlink()
    except OSError:
        pass


def student_can_view(exam, attempt):
    """Never during an attempt. 'before' = before start and after; 'after' = only once submitted."""
    if attempt and attempt["status"] == "in_progress":
        return False
    if attempt and attempt["status"] == "submitted":
        return True
    return exam["notes_visibility"] == "before"
