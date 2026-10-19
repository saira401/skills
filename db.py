"""SQLite schema + helpers. All queries are parameterised; writes go through tx()."""
import contextlib
import os
import sqlite3
import threading

from .config import cfg

_local = threading.local()

SCHEMA = """
-- Examiner accounts. pw_hash = PBKDF2-HMAC-SHA256(salt, password)
CREATE TABLE IF NOT EXISTS admins (
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at INTEGER NOT NULL);

-- Students identify with name + student ID. parent_* are optional and used to e-mail results.
CREATE TABLE IF NOT EXISTS students (
  id INTEGER PRIMARY KEY, student_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL, parent_name TEXT, parent_email TEXT, created_at INTEGER NOT NULL);

-- Question bank. type: mcq | multi | tf | fill. blank_answer holds accepted answers for fill ('a|b').
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY, text TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('mcq','multi','tf','fill')),
  category TEXT NOT NULL DEFAULT 'General',
  difficulty TEXT NOT NULL DEFAULT 'medium' CHECK (difficulty IN ('easy','medium','hard')),
  blank_answer TEXT, created_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS question_options (
  id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  text TEXT NOT NULL, is_correct INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL DEFAULT 0);

-- notes_visibility: 'before' (shown on the rules screen + result) | 'after' (result screen only).
-- Notes are never downloadable while an attempt is in progress.
CREATE TABLE IF NOT EXISTS exams (
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  topic TEXT NOT NULL DEFAULT '',
  time_limit_min INTEGER NOT NULL, pass_mark INTEGER NOT NULL,
  attempt_limit INTEGER NOT NULL DEFAULT 1,           -- DB constraint on attempts caps this at 1
  shuffle INTEGER NOT NULL DEFAULT 1, reveal_answers INTEGER NOT NULL DEFAULT 0,
  one_at_a_time INTEGER NOT NULL DEFAULT 1, send_parent INTEGER NOT NULL DEFAULT 1,
  notes_visibility TEXT NOT NULL DEFAULT 'after',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','open','closed')),
  created_by INTEGER, created_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS exam_questions (
  exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  position INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (exam_id, question_id));

-- Study notes uploaded by the examiner for an exam/topic. Files live in data/uploads/<stored_name>.
CREATE TABLE IF NOT EXISTS exam_notes (
  id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  topic TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL, stored_name TEXT NOT NULL,
  mime TEXT NOT NULL, size INTEGER NOT NULL, uploaded_at INTEGER NOT NULL);

-- One attempt per (exam, student) - enforced by the DB, not the client. Times are epoch ms.
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY, exam_id INTEGER NOT NULL REFERENCES exams(id),
  student_id INTEGER NOT NULL REFERENCES students(id),
  status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress','submitted')),
  started_at INTEGER NOT NULL, deadline_at INTEGER NOT NULL, submitted_at INTEGER,
  score REAL, max_score REAL, percent REAL, passed INTEGER,
  auto_submitted INTEGER NOT NULL DEFAULT 0, auto_reason TEXT,
  session_hash TEXT, ip TEXT, fp_hash TEXT, seed TEXT NOT NULL, last_seen INTEGER,
  UNIQUE (exam_id, student_id));

CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  response TEXT, delivered_at INTEGER, answered_at INTEGER,
  is_correct INTEGER, points_awarded REAL, flagged_fast INTEGER NOT NULL DEFAULT 0,
  UNIQUE (attempt_id, question_id));

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, detail TEXT, weight INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_attempt ON events(attempt_id);

-- Server-side sessions; only the SHA-256 of the cookie token is stored.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, role TEXT NOT NULL, user_id INTEGER NOT NULL, exam_id INTEGER,
  ip TEXT, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);

-- Outbox of result e-mails to parents. status: queued | saved (no SMTP: .eml written) | sent | failed | skipped
CREATE TABLE IF NOT EXISTS parent_emails (
  id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  to_addr TEXT, status TEXT NOT NULL, error TEXT, created_at INTEGER NOT NULL, sent_at INTEGER);
"""


def conn():
    path = cfg.DB_PATH
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        c = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn, _local.path, _local.depth = c, path, 0
    return c


def init():
    conn().executescript(SCHEMA)


def q(sql, params=(), one=False):
    """Read helper: returns list of dicts (or one dict / None)."""
    rows = conn().execute(sql, params).fetchall()
    if one:
        return dict(rows[0]) if rows else None
    return [dict(r) for r in rows]


def run(sql, params=()):
    """Write helper: returns the cursor (lastrowid / rowcount)."""
    return conn().execute(sql, params)


@contextlib.contextmanager
def tx():
    c = conn()
    if _local.depth > 0:  # re-entrant
        _local.depth += 1
        try:
            yield c
        finally:
            _local.depth -= 1
        return
    c.execute("BEGIN IMMEDIATE")
    _local.depth = 1
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    finally:
        _local.depth = 0
