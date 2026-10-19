"""SSE hub + live proctor snapshot builder."""
import queue
import threading
import time

from .. import db
from ..lib import risk

_subs = {}      # exam_id -> [queue.Queue]
_dirty = set()
_lock = threading.Lock()


def snapshot(exam_id):
    exam = db.q("SELECT id, title, code, status FROM exams WHERE id=?", (exam_id,), one=True)
    if not exam:
        return {"exam": None, "attempts": []}
    total = db.q("SELECT COUNT(*) n FROM exam_questions WHERE exam_id=?", (exam_id,), one=True)["n"]
    rows = db.q("SELECT a.*, s.name, s.student_code FROM attempts a JOIN students s ON s.id=a.student_id "
                "WHERE a.exam_id=? ORDER BY a.started_at", (exam_id,))
    kinds = {}
    for e in db.q("SELECT attempt_id, kind FROM events WHERE attempt_id IN (SELECT id FROM attempts WHERE exam_id=?)", (exam_id,)):
        kinds.setdefault(e["attempt_id"], []).append(e["kind"])
    answered = {r["attempt_id"]: r["n"] for r in db.q(
        "SELECT attempt_id, COUNT(*) n FROM answers WHERE answered_at IS NOT NULL AND attempt_id IN "
        "(SELECT id FROM attempts WHERE exam_id=?) GROUP BY attempt_id", (exam_id,))}
    now = int(time.time() * 1000)
    out = []
    for a in rows:
        r = risk.compute_risk(kinds.get(a["id"], []), bool(a["auto_submitted"]))
        n = answered.get(a["id"], 0)
        out.append({
            "attempt_id": a["id"], "name": a["name"], "student_id": a["student_code"], "status": a["status"],
            "active": a["status"] == "in_progress" and (a["last_seen"] or 0) > now - 20000,
            "progress": round(100 * n / total) if total else 0, "answered": n, "total": total,
            "time_left_s": max(0, (a["deadline_at"] - now) // 1000) if a["status"] == "in_progress" else 0,
            "risk": r["level"], "risk_score": r["score"], "focus_loss": r["focus_loss"], "strikes": r["strikes"],
            "auto_submitted": bool(a["auto_submitted"]),
        })
    return {"exam": exam, "attempts": out, "ts": now}


def subscribe(exam_id):
    sub = queue.Queue(maxsize=20)
    sub.put(snapshot(exam_id))
    with _lock:
        _subs.setdefault(exam_id, []).append(sub)
    return sub


def unsubscribe(exam_id, sub):
    with _lock:
        if sub in _subs.get(exam_id, []):
            _subs[exam_id].remove(sub)


def mark(exam_id):
    with _lock:
        _dirty.add(exam_id)


def tick(force=False):
    """Called ~1/s by the sweep thread: push fresh snapshots to subscribed dashboards."""
    with _lock:
        targets = [e for e, s in _subs.items() if s and (force or e in _dirty or int(time.time()) % 3 == 0)]
        _dirty.clear()
    for exam_id in targets:
        snap = snapshot(exam_id)
        with _lock:
            for sub in list(_subs.get(exam_id, [])):
                try:
                    sub.put_nowait(snap)
                except queue.Full:
                    pass
