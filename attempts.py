"""Attempt lifecycle: start, deliver, answer, events, grade (finalize), sweep. The server owns the clock."""
import hashlib
import json
import secrets
import sqlite3
import time

from .. import db
from ..config import cfg
from ..db import q, run, tx
from ..lib import risk, shuffle
from ..lib.router import HttpError
from . import live


def now_ms():
    return int(time.time() * 1000)


def norm(s):
    return " ".join(str(s).casefold().split())


# ---------- helpers ----------
def get_attempt(exam_id, student_id):
    return q("SELECT * FROM attempts WHERE exam_id=? AND student_id=?", (exam_id, student_id), one=True)


def ordered_ids(attempt, exam):
    ids = [r["question_id"] for r in q("SELECT question_id FROM exam_questions WHERE exam_id=? ORDER BY position", (exam["id"],))]
    return shuffle.seeded_shuffle(ids, f"{attempt['seed']}:q") if exam["shuffle"] else ids


def payload(attempt, exam, qid):
    """What the browser may see. No is_correct, no blank_answer - ever."""
    qu = q("SELECT id, text, type, category FROM questions WHERE id=?", (qid,), one=True)
    opts = [{"id": o["id"], "text": o["text"]} for o in
            q("SELECT id, text FROM question_options WHERE question_id=? ORDER BY position, id", (qid,))]
    if exam["shuffle"] and qu["type"] in ("mcq", "multi"):
        opts = shuffle.seeded_shuffle(opts, f"{attempt['seed']}:o{qid}")
    pts = q("SELECT points FROM exam_questions WHERE exam_id=? AND question_id=?", (exam["id"], qid), one=True)["points"]
    return {"id": qid, "type": qu["type"], "text": qu["text"], "category": qu["category"], "points": pts,
            "options": opts if qu["type"] != "fill" else []}


def kinds_for(attempt_id):
    return [r["kind"] for r in q("SELECT kind FROM events WHERE attempt_id=?", (attempt_id,))]


def attempt_risk(attempt):
    return risk.compute_risk(kinds_for(attempt["id"]), bool(attempt["auto_submitted"]))


def add_event(attempt_id, kind, detail=""):
    run("INSERT INTO events(attempt_id, kind, detail, weight, created_at) VALUES (?,?,?,?,?)",
        (attempt_id, kind, (detail or "")[:300], risk.WEIGHTS.get(kind, 0), now_ms()))


def touch(attempt):
    run("UPDATE attempts SET last_seen=? WHERE id=?", (now_ms(), attempt["id"]))


# ---------- start / deliver ----------
def start(exam, student, ip, fp, session_hash):
    if exam["status"] != "open":
        raise HttpError(403, "This exam is not open")
    t = now_ms()
    try:
        with tx():
            cur = run("INSERT INTO attempts(exam_id, student_id, started_at, deadline_at, session_hash, ip, fp_hash, seed, last_seen) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (exam["id"], student["id"], t, t + exam["time_limit_min"] * 60000, session_hash, ip,
                       hashlib.sha256((fp or "").encode()).hexdigest() if fp else None, secrets.token_hex(8), t))
            aid = cur.lastrowid
            if q("SELECT 1 FROM attempts WHERE exam_id=? AND ip=? AND id<>?", (exam["id"], ip, aid), one=True):
                add_event(aid, "same_ip", ip)
    except sqlite3.IntegrityError:
        raise HttpError(409, "You have already attempted this exam", code="already_attempted")
    live.mark(exam["id"])
    return q("SELECT * FROM attempts WHERE id=?", (aid,), one=True)


def deliver(attempt, exam):
    """Return questions to show now and mark them delivered (starts the 'too fast' clock)."""
    ids = ordered_ids(attempt, exam)
    rows = {r["question_id"]: r for r in q("SELECT * FROM answers WHERE attempt_id=?", (attempt["id"],))}
    if exam["one_at_a_time"]:
        todo = [i for i in ids if not (rows.get(i) or {}).get("answered_at")][:1]
    else:
        todo = ids
    t = now_ms()
    for i in todo:
        run("INSERT OR IGNORE INTO answers(attempt_id, question_id, delivered_at) VALUES (?,?,?)", (attempt["id"], i, t))
    answered = [i for i in ids if (rows.get(i) or {}).get("answered_at")]
    saved = {}
    for i in todo:
        r = rows.get(i)
        if r and r["response"]:
            saved[i] = json.loads(r["response"])
    return {"questions": [dict(payload(attempt, exam, i), saved=saved.get(i)) for i in todo],
            "answered_ids": answered, "total": len(ids), "answered": len(answered)}


# ---------- answering ----------
def _check_response(qu, opt_ids, resp):
    if not isinstance(resp, dict):
        raise HttpError(400, "Invalid response")
    if qu["type"] in ("mcq", "tf"):
        if resp.get("choice") not in opt_ids:
            raise HttpError(400, "Invalid choice")
        return {"choice": resp["choice"]}
    if qu["type"] == "multi":
        ch = resp.get("choices")
        if not isinstance(ch, list) or len(set(ch)) != len(ch) or any(c not in opt_ids for c in ch):
            raise HttpError(400, "Invalid choices")
        return {"choices": sorted(ch)}
    text = resp.get("text")
    if not isinstance(text, str) or len(text) > 200:
        raise HttpError(400, "Answer must be text up to 200 characters")
    return {"text": text.strip()}


def answer(attempt, exam, qid, resp):
    t = now_ms()
    if attempt["status"] != "in_progress":
        raise HttpError(409, "Attempt already submitted", code="submitted")
    if t > attempt["deadline_at"]:
        finalize(attempt["id"], auto=True, reason="time")
        raise HttpError(409, "Time is up - your exam was submitted", code="time_up")
    row = q("SELECT * FROM answers WHERE attempt_id=? AND question_id=?", (attempt["id"], qid), one=True)
    if not row or not row["delivered_at"]:
        raise HttpError(409, "Question was not delivered", code="not_delivered")
    if exam["one_at_a_time"] and row["answered_at"]:
        raise HttpError(409, "Already answered", code="already_answered")
    qu = q("SELECT * FROM questions WHERE id=?", (qid,), one=True)
    opt_ids = {o["id"] for o in q("SELECT id FROM question_options WHERE question_id=?", (qid,))}
    clean = _check_response(qu, opt_ids, resp)
    last = q("SELECT MAX(answered_at) m FROM answers WHERE attempt_id=?", (attempt["id"],), one=True)["m"] or 0
    fast = t - max(row["delivered_at"], last) < cfg.FAST_ANSWER_MS
    with tx():
        run("UPDATE answers SET response=?, answered_at=?, flagged_fast=? WHERE id=?", (json.dumps(clean), t, int(fast), row["id"]))
        if fast:
            add_event(attempt["id"], "fast_answer", f"q{qid} {t - max(row['delivered_at'], last)}ms")
        if qu["type"] == "fill" and clean["text"]:
            _check_similarity(attempt, exam, qu, clean["text"])
        touch(attempt)
    live.mark(exam["id"])
    n = q("SELECT COUNT(*) n FROM answers WHERE attempt_id=? AND answered_at IS NOT NULL", (attempt["id"],), one=True)["n"]
    return {"answered": n}


def _check_similarity(attempt, exam, qu, text):
    """Fill-in-blank: identical or near-identical WRONG answers from different students are suspicious."""
    accepted = [norm(a) for a in (qu["blank_answer"] or "").split("|")]
    mine = norm(text)
    if mine in accepted or len(mine) < 4:
        return
    others = q("SELECT a.response FROM answers a JOIN attempts t ON t.id=a.attempt_id "
               "WHERE t.exam_id=? AND a.question_id=? AND a.attempt_id<>? AND a.response IS NOT NULL",
               (exam["id"], qu["id"], attempt["id"]))
    for o in others:
        theirs = norm(json.loads(o["response"]).get("text", ""))
        if len(theirs) >= 4 and theirs not in accepted and risk.levenshtein(mine, theirs) <= max(1, len(mine) // 8):
            add_event(attempt["id"], "similar_answer", f"q{qu['id']} matches another student's wrong answer")
            return


# ---------- events ----------
def record_event(attempt, exam, kind, detail="", trusted=False):
    """trusted=True is for server-originated events (e.g. session_takeover); browsers may only send CLIENT_KINDS."""
    if attempt["status"] != "in_progress":
        raise HttpError(409, "Attempt already submitted", code="submitted")
    if kind not in risk.CLIENT_KINDS and not trusted:
        raise HttpError(400, "Unknown event")
    if q("SELECT COUNT(*) n FROM events WHERE attempt_id=?", (attempt["id"],), one=True)["n"] < 300:
        add_event(attempt["id"], kind, detail)
    touch(attempt)
    strikes = sum(1 for k in kinds_for(attempt["id"]) if k in risk.STRIKE_KINDS)
    submitted = False
    if strikes >= cfg.MAX_STRIKES:
        finalize(attempt["id"], auto=True, reason="strike limit")
        submitted = True
    live.mark(exam["id"])
    return {"strikes": strikes, "max_strikes": cfg.MAX_STRIKES, "submitted": submitted}


# ---------- grading ----------
def grade(qu, options, response):
    if not response:
        return False
    if qu["type"] in ("mcq", "tf"):
        return any(o["id"] == response.get("choice") and o["is_correct"] for o in options)
    if qu["type"] == "multi":
        return set(response.get("choices", [])) == {o["id"] for o in options if o["is_correct"]}
    return norm(response.get("text", "")) in [norm(a) for a in (qu["blank_answer"] or "").split("|")]


def finalize(attempt_id, auto=False, reason=""):
    """The ONLY place scores are computed. Idempotent."""
    mailed = False
    with tx():
        a = q("SELECT * FROM attempts WHERE id=?", (attempt_id,), one=True)
        if a["status"] != "in_progress":
            return a
        exam = q("SELECT * FROM exams WHERE id=?", (a["exam_id"],), one=True)
        score = total = 0
        for eq in q("SELECT * FROM exam_questions WHERE exam_id=?", (exam["id"],)):
            qu = q("SELECT * FROM questions WHERE id=?", (eq["question_id"],), one=True)
            opts = q("SELECT * FROM question_options WHERE question_id=?", (qu["id"],))
            row = q("SELECT * FROM answers WHERE attempt_id=? AND question_id=?", (attempt_id, qu["id"]), one=True)
            resp = json.loads(row["response"]) if row and row["response"] else None
            ok = grade(qu, opts, resp)
            total += eq["points"]
            score += eq["points"] if ok else 0
            if row:
                run("UPDATE answers SET is_correct=?, points_awarded=? WHERE id=?", (int(ok), eq["points"] if ok else 0, row["id"]))
            else:
                run("INSERT INTO answers(attempt_id, question_id, is_correct, points_awarded) VALUES (?,?,?,?)",
                    (attempt_id, qu["id"], 0, 0))
        pct = round(100 * score / total, 2) if total else 0
        run("UPDATE attempts SET status='submitted', submitted_at=?, score=?, max_score=?, percent=?, passed=?, "
            "auto_submitted=?, auto_reason=? WHERE id=?",
            (now_ms(), score, total, pct, int(pct >= exam["pass_mark"]), int(auto), reason if auto else None, attempt_id))
        if auto:
            add_event(attempt_id, "auto_submit", reason)
        mailed = bool(exam["send_parent"])
    live.mark(a["exam_id"])
    if mailed:
        from . import mailer
        mailer.notify_parent(attempt_id)
    return q("SELECT * FROM attempts WHERE id=?", (attempt_id,), one=True)


def sweep():
    """Auto-submit attempts whose server-side deadline has passed."""
    for r in q("SELECT id FROM attempts WHERE status='in_progress' AND deadline_at < ?", (now_ms(),)):
        finalize(r["id"], auto=True, reason="time")


# ---------- result views ----------
def answer_rows(attempt_id):
    """Per-question breakdown incl. correct answers (admin, or student when reveal is on)."""
    out = []
    for r in q("SELECT a.*, qu.text, qu.type, qu.blank_answer, eq.position, eq.points FROM answers a "
               "JOIN questions qu ON qu.id=a.question_id JOIN attempts t ON t.id=a.attempt_id "
               "JOIN exam_questions eq ON eq.exam_id=t.exam_id AND eq.question_id=a.question_id "
               "WHERE a.attempt_id=? ORDER BY eq.position", (attempt_id,)):
        opts = q("SELECT id, text, is_correct FROM question_options WHERE question_id=? ORDER BY position, id", (r["question_id"],))
        resp = json.loads(r["response"]) if r["response"] else None
        text = {o["id"]: o["text"] for o in opts}
        if r["type"] == "fill":
            given, correct = (resp or {}).get("text", ""), (r["blank_answer"] or "").replace("|", " / ")
        elif r["type"] == "multi":
            given = ", ".join(text.get(i, "?") for i in (resp or {}).get("choices", []))
            correct = ", ".join(o["text"] for o in opts if o["is_correct"])
        else:
            given = text.get((resp or {}).get("choice"), "")
            correct = ", ".join(o["text"] for o in opts if o["is_correct"])
        out.append({"question_id": r["question_id"], "text": r["text"], "type": r["type"], "given": given or "(no answer)",
                    "correct_answer": correct, "is_correct": bool(r["is_correct"]), "points": r["points_awarded"] or 0,
                    "max_points": r["points"], "flagged_fast": bool(r["flagged_fast"])})
    return out


def summary(attempt):
    exam = q("SELECT * FROM exams WHERE id=?", (attempt["exam_id"],), one=True)
    stu = q("SELECT * FROM students WHERE id=?", (attempt["student_id"],), one=True)
    r = attempt_risk(attempt)
    return {"attempt_id": attempt["id"], "exam": exam["title"], "name": stu["name"], "student_id": stu["student_code"],
            "status": attempt["status"], "score": attempt["score"], "max_score": attempt["max_score"],
            "percent": attempt["percent"], "passed": bool(attempt["passed"]), "pass_mark": exam["pass_mark"],
            "started_at": attempt["started_at"], "submitted_at": attempt["submitted_at"],
            "auto_submitted": bool(attempt["auto_submitted"]), "auto_reason": attempt["auto_reason"],
            "risk": r["level"], "risk_score": r["score"], "strikes": r["strikes"], "focus_loss": r["focus_loss"]}
