"""Student API: join, state, start, questions, answer, event, submit, result, notes."""
import time

from .. import db
from ..config import cfg
from ..db import q, run
from ..lib import risk, sessions, validate as V
from ..lib.router import R, HttpError, Response
from ..services import attempts, live, mailer, notes
from .auth import set_session_cookie

S = "student"


def mask_email(e):
    if not e or "@" not in e:
        return None
    u, d = e.split("@", 1)
    return u[:1] + "***@" + d


def ctx(req, strict=True):
    """(exam, student, attempt) for the logged-in student. strict: reject sessions replaced by a newer login."""
    s = req.session
    exam = q("SELECT * FROM exams WHERE id=?", (s["exam_id"],), one=True)
    stu = q("SELECT * FROM students WHERE id=?", (s["user_id"],), one=True)
    if not exam or not stu:
        raise HttpError(401, "Session invalid")
    a = attempts.get_attempt(exam["id"], stu["id"])
    if strict and a and a["status"] == "in_progress" and a["session_hash"] != s["token_hash"]:
        raise HttpError(409, "This session was replaced by a newer login", code="session_replaced")
    return exam, stu, a


def state_payload(exam, stu, a):
    st = "submitted" if a and a["status"] == "submitted" else "in_progress" if a else "ready"
    out = {"state": st, "exam": {"title": exam["title"], "topic": exam["topic"], "time_limit_min": exam["time_limit_min"],
                                 "pass_mark": exam["pass_mark"], "one_at_a_time": bool(exam["one_at_a_time"]),
                                 "max_strikes": cfg.MAX_STRIKES, "status": exam["status"],
                                 "question_count": q("SELECT COUNT(*) n FROM exam_questions WHERE exam_id=?", (exam["id"],), one=True)["n"]},
           "student": {"name": stu["name"], "student_id": stu["student_code"], "parent_email": mask_email(stu["parent_email"]),
                      "parent_will_be_emailed": bool(exam["send_parent"] and stu["parent_email"])}}
    out["notes"] = notes.list_for(exam["id"]) if notes.student_can_view(exam, a) else []
    return out


@R.post("/api/student/join")
def join(req):
    d = req.json()
    code = V.str_(d.get("code"), "exam code", 6, 6).upper()
    name = V.str_(d.get("name"), "name", 2, 80)
    sid = V.str_(d.get("student_id"), "student ID", 2, 40)
    p_email = V.email(d["parent_email"], "parent e-mail") if d.get("parent_email") else None
    p_name = V.str_(d["parent_name"], "parent name", 1, 80) if d.get("parent_name") else None
    exam = q("SELECT * FROM exams WHERE code=?", (code,), one=True)
    if not exam or exam["status"] == "draft":
        raise HttpError(404, "Exam code not found")
    stu = q("SELECT * FROM students WHERE student_code=?", (sid,), one=True)
    if stu:
        if " ".join(stu["name"].casefold().split()) != " ".join(name.casefold().split()):
            raise HttpError(403, "That student ID is registered under a different name")
        if p_email or p_name:
            run("UPDATE students SET parent_email=COALESCE(?, parent_email), parent_name=COALESCE(?, parent_name) WHERE id=?",
                (p_email, p_name, stu["id"]))
    else:
        sidn = run("INSERT INTO students(student_code, name, parent_name, parent_email, created_at) VALUES (?,?,?,?,?)",
                   (sid, name, p_name, p_email, int(time.time()))).lastrowid
        stu = q("SELECT * FROM students WHERE id=?", (sidn,), one=True)
    stu = q("SELECT * FROM students WHERE id=?", (stu["id"],), one=True)
    a = attempts.get_attempt(exam["id"], stu["id"])
    if not a and exam["status"] != "open":
        raise HttpError(403, "This exam is closed")
    old = req.session if req.session and req.session["role"] == "student" else None
    resume = bool(old and a and old["user_id"] == stu["id"] and old["exam_id"] == exam["id"] and a["session_hash"] == old["token_hash"])
    if req.session:
        sessions.destroy(req.cookie_token)  # regenerate on every login
    token = sessions.create("student", stu["id"], exam["id"], req.ip)
    extra = {}
    if a and a["status"] == "in_progress":
        run("UPDATE attempts SET session_hash=? WHERE id=?", (sessions.hash_token(token), a["id"]))
        if not resume:  # someone logged in again while the attempt is running: strike + old session locked
            a = q("SELECT * FROM attempts WHERE id=?", (a["id"],), one=True)
            extra = attempts.record_event(a, exam, "session_takeover", "new login for running attempt", trusted=True)
        a = attempts.get_attempt(exam["id"], stu["id"])
    resp = Response.json(dict(state_payload(exam, stu, a), **extra))
    set_session_cookie(resp, token)
    return resp


@R.get("/api/student/state", S)
def state(req):
    exam, stu, a = ctx(req, strict=False)
    if a and a["status"] == "in_progress" and a["session_hash"] != req.session["token_hash"]:
        return {"state": "replaced"}
    return state_payload(exam, stu, a)


@R.post("/api/student/start", S)
def start(req):
    exam, stu, a = ctx(req)
    if a:
        raise HttpError(409, "You have already attempted this exam", code="already_attempted")
    fp = req.json().get("fp") if req.body else None
    attempts.start(exam, stu, req.ip, fp if isinstance(fp, str) else None, req.session["token_hash"])
    return {"ok": True}


def _live_attempt(req):
    exam, stu, a = ctx(req)
    if not a:
        raise HttpError(409, "Exam not started", code="not_started")
    return exam, a


@R.get("/api/student/questions", S)
def questions(req):
    exam, a = _live_attempt(req)
    if a["status"] != "in_progress":
        return {"state": "submitted"}
    if attempts.now_ms() > a["deadline_at"]:
        attempts.finalize(a["id"], auto=True, reason="time")
        return {"state": "submitted"}
    attempts.touch(a)
    data = attempts.deliver(a, exam)
    data.update(state="in_progress", remaining_ms=max(0, a["deadline_at"] - attempts.now_ms()),
                strikes=attempts.attempt_risk(a)["strikes"], max_strikes=cfg.MAX_STRIKES, one_at_a_time=bool(exam["one_at_a_time"]))
    return data


@R.post("/api/student/answer", S)
def answer(req):
    exam, a = _live_attempt(req)
    d = req.json()
    return attempts.answer(a, exam, V.int_(d.get("question_id"), "question_id", 1, 10**9), d.get("response"))


@R.post("/api/student/event", S)
def event(req):
    exam, a = _live_attempt(req)
    d = req.json()
    return attempts.record_event(a, exam, str(d.get("kind")), str(d.get("detail") or ""))


@R.get("/api/student/ping", S)
def ping(req):
    exam, a = _live_attempt(req)
    if a["status"] == "in_progress" and attempts.now_ms() > a["deadline_at"]:
        attempts.finalize(a["id"], auto=True, reason="time")
        a = q("SELECT * FROM attempts WHERE id=?", (a["id"],), one=True)
    if a["status"] == "in_progress":
        attempts.touch(a)
    return {"status": a["status"], "remaining_ms": max(0, a["deadline_at"] - attempts.now_ms()),
            "strikes": attempts.attempt_risk(a)["strikes"]}


@R.post("/api/student/submit", S)
def submit(req):
    exam, a = _live_attempt(req)
    attempts.finalize(a["id"])  # grading happens here, on the server; any client score claim is ignored
    return result_payload(exam, q("SELECT * FROM attempts WHERE id=?", (a["id"],), one=True))


def result_payload(exam, a):
    out = {"state": "submitted", "exam": exam["title"], "score": a["score"], "max_score": a["max_score"], "percent": a["percent"],
           "passed": bool(a["passed"]), "pass_mark": exam["pass_mark"], "auto_submitted": bool(a["auto_submitted"]),
           "auto_reason": a["auto_reason"], "review": None}
    if exam["reveal_answers"]:
        out["review"] = [{k: r[k] for k in ("text", "given", "correct_answer", "is_correct", "points", "max_points")}
                         for r in attempts.answer_rows(a["id"])]
    m = mailer.last_status(a["id"]) if exam["send_parent"] else None
    out["parent_email"] = {"to": mask_email(m["to_addr"]), "status": m["status"]} if m else None
    out["notes"] = notes.list_for(exam["id"])
    return out


@R.get("/api/student/result", S)
def result(req):
    exam, a = _live_attempt(req)
    if a["status"] != "submitted":
        raise HttpError(409, "Exam not submitted yet")
    return result_payload(exam, a)


@R.get("/api/student/notes/<int:id>", S)
def student_note(req):
    exam, stu, a = ctx(req)
    if not notes.student_can_view(exam, a):
        raise HttpError(403, "Notes are not available right now")
    n, data = notes.read(req.params["id"], exam["id"])
    from ..services.reports import safe_name
    return Response(data, 200, n["mime"], [("Content-Disposition", f'attachment; filename="{safe_name(n["filename"])}"')])
