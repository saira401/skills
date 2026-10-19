"""Examiner API: question bank, exams, notes, live SSE, results, PDF/CSV, parent e-mail."""
import json
import queue
import re
import secrets
import time
from urllib.parse import unquote

from .. import db
from ..config import cfg
from ..db import q, run, tx
from ..lib import qr, validate as V
from ..lib.router import R, HttpError, Response, Stream
from ..services import attempts, live, mailer, notes, reports

A = "admin"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ---------- question bank ----------
def _question(qid):
    qu = q("SELECT * FROM questions WHERE id=?", (qid,), one=True)
    if not qu:
        raise HttpError(404, "Question not found")
    qu["options"] = q("SELECT id, text, is_correct FROM question_options WHERE question_id=? ORDER BY position, id", (qid,))
    return qu


def _save_options(qid, opts):
    run("DELETE FROM question_options WHERE question_id=?", (qid,))
    for i, o in enumerate(opts):
        run("INSERT INTO question_options(question_id, text, is_correct, position) VALUES (?,?,?,?)", (qid, o["text"], int(o["correct"]), i))


@R.get("/api/admin/questions", A)
def list_questions(req):
    sql, args = "SELECT * FROM questions", []
    cond = []
    for key in ("category", "difficulty", "type"):
        if req.query.get(key):
            cond.append(f"{key}=?")
            args.append(req.query[key])
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    return {"questions": q(sql + " ORDER BY id DESC", tuple(args))}


@R.post("/api/admin/questions", A)
def create_question(req):
    d = V.parse_question(req.json())
    with tx():
        qid = run("INSERT INTO questions(text, type, category, difficulty, blank_answer, created_at) VALUES (?,?,?,?,?,?)",
                  (d["text"], d["type"], d["category"], d["difficulty"], d["blank_answer"], int(time.time()))).lastrowid
        _save_options(qid, d["options"])
    return Response.json(_question(qid), 201)


@R.get("/api/admin/questions/<int:id>", A)
def get_question(req):
    return _question(req.params["id"])


@R.put("/api/admin/questions/<int:id>", A)
def update_question(req):
    qid = req.params["id"]
    _question(qid)
    d = V.parse_question(req.json())
    with tx():
        run("UPDATE questions SET text=?, type=?, category=?, difficulty=?, blank_answer=? WHERE id=?",
            (d["text"], d["type"], d["category"], d["difficulty"], d["blank_answer"], qid))
        _save_options(qid, d["options"])
    return _question(qid)


@R.delete("/api/admin/questions/<int:id>", A)
def delete_question(req):
    qid = req.params["id"]
    _question(qid)
    if q("SELECT 1 FROM exam_questions WHERE question_id=?", (qid,), one=True):
        raise HttpError(409, "Question is used by an exam - remove it from the exam first")
    run("DELETE FROM questions WHERE id=?", (qid,))
    return {"ok": True}


# ---------- exams ----------
def _exam(eid):
    e = q("SELECT * FROM exams WHERE id=?", (eid,), one=True)
    if not e:
        raise HttpError(404, "Exam not found")
    return e


def _exam_full(eid):
    e = _exam(eid)
    e["questions"] = q("SELECT eq.question_id id, eq.points, qu.text, qu.type FROM exam_questions eq "
                       "JOIN questions qu ON qu.id=eq.question_id WHERE eq.exam_id=? ORDER BY eq.position", (eid,))
    e["notes"] = notes.list_for(eid)
    e["attempt_count"] = q("SELECT COUNT(*) n FROM attempts WHERE exam_id=?", (eid,), one=True)["n"]
    e["join_url"] = f"{cfg.base_url()}/?code={e['code']}"
    return e


def _set_questions(eid, qs):
    for item in qs:
        if not q("SELECT 1 FROM questions WHERE id=?", (item["id"],), one=True):
            raise HttpError(400, f"Question {item['id']} does not exist")
    run("DELETE FROM exam_questions WHERE exam_id=?", (eid,))
    for pos, item in enumerate(qs):
        run("INSERT INTO exam_questions(exam_id, question_id, position, points) VALUES (?,?,?,?)", (eid, item["id"], pos, item["points"]))


def _new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not q("SELECT 1 FROM exams WHERE code=?", (code,), one=True):
            return code


@R.get("/api/admin/exams", A)
def list_exams(req):
    rows = q("SELECT e.*, (SELECT COUNT(*) FROM exam_questions WHERE exam_id=e.id) question_count, "
             "(SELECT COUNT(*) FROM attempts WHERE exam_id=e.id) attempt_count FROM exams e ORDER BY id DESC")
    return {"exams": rows}


@R.post("/api/admin/exams", A)
def create_exam(req):
    d = V.parse_exam(req.json())
    with tx():
        eid = run("INSERT INTO exams(code, title, topic, time_limit_min, pass_mark, attempt_limit, shuffle, reveal_answers, "
                  "one_at_a_time, send_parent, notes_visibility, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (_new_code(), d["title"], d["topic"], d["time_limit_min"], d["pass_mark"], 1, d["shuffle"], d["reveal_answers"],
                   d["one_at_a_time"], d["send_parent"], d["notes_visibility"], req.session["user_id"], int(time.time()))).lastrowid
        _set_questions(eid, d["questions"])
    return Response.json(_exam_full(eid), 201)


@R.get("/api/admin/exams/<int:id>", A)
def get_exam(req):
    return _exam_full(req.params["id"])


@R.put("/api/admin/exams/<int:id>", A)
def update_exam(req):
    eid = req.params["id"]
    _exam(eid)
    d = V.parse_exam(req.json())
    has_attempts = q("SELECT 1 FROM attempts WHERE exam_id=?", (eid,), one=True)
    with tx():
        run("UPDATE exams SET title=?, topic=?, time_limit_min=?, pass_mark=?, shuffle=?, reveal_answers=?, one_at_a_time=?, "
            "send_parent=?, notes_visibility=? WHERE id=?",
            (d["title"], d["topic"], d["time_limit_min"], d["pass_mark"], d["shuffle"], d["reveal_answers"], d["one_at_a_time"],
             d["send_parent"], d["notes_visibility"], eid))
        current = [(r["question_id"], r["points"]) for r in q("SELECT question_id, points FROM exam_questions WHERE exam_id=? ORDER BY position", (eid,))]
        if [(x["id"], x["points"]) for x in d["questions"]] != current:
            if has_attempts:
                raise HttpError(409, "Questions cannot change after students have attempted the exam")
            _set_questions(eid, d["questions"])
    live.mark(eid)
    return _exam_full(eid)


@R.delete("/api/admin/exams/<int:id>", A)
def delete_exam(req):
    eid = req.params["id"]
    _exam(eid)
    if q("SELECT 1 FROM attempts WHERE exam_id=?", (eid,), one=True):
        raise HttpError(409, "Exam has attempts and cannot be deleted")
    for n in notes.list_for(eid):
        notes.delete(n["id"])
    run("DELETE FROM exams WHERE id=?", (eid,))
    return {"ok": True}


@R.post("/api/admin/exams/<int:id>/status", A)
def set_status(req):
    eid = req.params["id"]
    e = _exam(eid)
    status = req.json().get("status")
    if status not in ("draft", "open", "closed"):
        raise HttpError(400, "status must be draft, open or closed")
    if status == "open" and not q("SELECT 1 FROM exam_questions WHERE exam_id=?", (eid,), one=True):
        raise HttpError(400, "Add questions before opening the exam")
    if status == "draft" and q("SELECT 1 FROM attempts WHERE exam_id=?", (eid,), one=True):
        raise HttpError(409, "Exam already has attempts")
    run("UPDATE exams SET status=? WHERE id=?", (status, eid))
    if status == "closed":  # stop = submit everything still running
        for a in q("SELECT id FROM attempts WHERE exam_id=? AND status='in_progress'", (eid,)):
            attempts.finalize(a["id"], auto=True, reason="exam closed")
    live.mark(eid)
    return {"ok": True, "status": status}


@R.get("/api/admin/exams/<int:id>/qr.png", A)
def exam_qr(req):
    e = _exam(req.params["id"])
    png = qr.png(qr.make(f"{cfg.base_url()}/?code={e['code']}"))
    headers = [("Content-Disposition", f'attachment; filename="examshield-{e["code"]}.png"')] if req.query.get("download") else []
    return Response(png, 200, "image/png", headers)


# ---------- live (SSE) ----------
@R.get("/api/admin/exams/<int:id>/live", A)
def live_stream(req):
    eid = req.params["id"]
    _exam(eid)
    sub = live.subscribe(eid)

    def gen():
        try:
            yield b"retry: 3000\n\n"
            while True:
                try:
                    yield b"data: " + json.dumps(sub.get(timeout=15)).encode() + b"\n\n"
                except queue.Empty:
                    yield b": keep-alive\n\n"
        finally:
            live.unsubscribe(eid, sub)
    return Stream(gen())


# ---------- results ----------
def _rows(eid):
    rows = q("SELECT a.*, s.name, s.student_code, s.parent_email FROM attempts a JOIN students s ON s.id=a.student_id WHERE a.exam_id=?", (eid,))
    out = []
    for a in rows:
        s = attempts.summary(a)
        s["parent_email"] = a["parent_email"] or ""
        m = mailer.last_status(a["id"])
        s["parent_email_status"] = m["status"] if m else ("none" if not a["parent_email"] else "pending")
        s["duration_s"] = ((a["submitted_at"] or a["deadline_at"]) - a["started_at"]) // 1000
        out.append(s)
    done = sorted([r for r in out if r["status"] == "submitted"], key=lambda r: (-(r["score"] or 0), r["duration_s"]))
    for i, r in enumerate(done, 1):
        r["rank"] = i
    for r in out:
        r.setdefault("rank", "")
    return sorted(out, key=lambda r: (r["rank"] == "", r["rank"] or 0))


@R.get("/api/admin/exams/<int:id>/results", A)
def results(req):
    e = _exam(req.params["id"])
    return {"exam": {"id": e["id"], "title": e["title"], "code": e["code"], "send_parent": e["send_parent"]}, "rows": _rows(e["id"])}


def _attempt(aid):
    a = q("SELECT * FROM attempts WHERE id=?", (aid,), one=True)
    if not a:
        raise HttpError(404, "Attempt not found")
    return a


@R.get("/api/admin/attempts/<int:id>", A)
def attempt_detail(req):
    a = _attempt(req.params["id"])
    stu = q("SELECT parent_name, parent_email FROM students WHERE id=?", (a["student_id"],), one=True)
    return {"summary": attempts.summary(a), "answers": attempts.answer_rows(a["id"]) if a["status"] == "submitted" else [],
            "events": q("SELECT kind, detail, weight, created_at FROM events WHERE attempt_id=? ORDER BY id", (a["id"],)),
            "parent": stu, "parent_emails": q("SELECT to_addr, status, error, created_at FROM parent_emails WHERE attempt_id=? ORDER BY id DESC", (a["id"],))}


def _download(data, ctype, name):
    return Response(data, 200, ctype, [("Content-Disposition", f'attachment; filename="{reports.safe_name(name)}"')])


@R.get("/api/admin/attempts/<int:id>/pdf", A)
def attempt_pdf(req):
    a = _attempt(req.params["id"])
    return _download(reports.student_report(a["id"]), "application/pdf", f"result-{a['id']}.pdf")


@R.get("/api/admin/exams/<int:id>/pdf.zip", A)
def exam_zip(req):
    e = _exam(req.params["id"])
    return _download(reports.bulk_zip(e["id"]), "application/zip", f"{e['code']}-reports.zip")


@R.get("/api/admin/exams/<int:id>/results.csv", A)
def exam_csv(req):
    e = _exam(req.params["id"])
    return _download(reports.results_csv(_rows(e["id"])), "text/csv; charset=utf-8", f"{e['code']}-results.csv")


# ---------- parent e-mail ----------
@R.post("/api/admin/attempts/<int:id>/send-parent", A)
def send_parent(req):
    a = _attempt(req.params["id"])
    if a["status"] != "submitted":
        raise HttpError(409, "Attempt is not submitted yet")
    d = req.json()
    to = V.email(d["email"], "parent e-mail") if d.get("email") else None
    if to:
        run("UPDATE students SET parent_email=? WHERE id=?", (to, a["student_id"]))
    mailer.notify_parent(a["id"], to=to, force=True)
    return {"ok": True, "status": mailer.last_status(a["id"])}


@R.post("/api/admin/exams/<int:id>/send-parents", A)
def send_all_parents(req):
    e = _exam(req.params["id"])
    n = 0
    for a in q("SELECT a.id FROM attempts a JOIN students s ON s.id=a.student_id WHERE a.exam_id=? AND a.status='submitted' "
               "AND s.parent_email IS NOT NULL AND s.parent_email<>''", (e["id"],)):
        mailer.notify_parent(a["id"], force=True)
        n += 1
    return {"ok": True, "queued": n}


# ---------- study notes ----------
@R.get("/api/admin/exams/<int:id>/notes", A)
def list_notes(req):
    _exam(req.params["id"])
    return {"notes": notes.list_for(req.params["id"])}


@R.post("/api/admin/exams/<int:id>/notes", A)
def upload_note(req):
    """Raw body upload: X-Filename (URL-encoded) + optional X-Topic headers, body = file bytes."""
    eid = req.params["id"]
    e = _exam(eid)
    fname = unquote(req.headers.get("X-Filename", ""))
    topic = unquote(req.headers.get("X-Topic", "")) or e["topic"]
    nid = notes.save(eid, fname, topic, req.body)
    return Response.json({"ok": True, "id": nid}, 201)


@R.delete("/api/admin/notes/<int:id>", A)
def delete_note(req):
    notes.delete(req.params["id"])
    return {"ok": True}


@R.get("/api/admin/notes/<int:id>/download", A)
def download_note(req):
    n, data = notes.read(req.params["id"])
    return Response(data, 200, n["mime"], [("Content-Disposition", f'attachment; filename="{reports.safe_name(n["filename"])}"')])
