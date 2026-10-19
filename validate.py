"""Input validators. Every external value passes through here."""
import re

from .router import HttpError

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
QTYPES = ("mcq", "multi", "tf", "fill")
DIFFS = ("easy", "medium", "hard")


def str_(v, name, min_len=1, max_len=200):
    if not isinstance(v, str):
        raise HttpError(400, f"{name} must be text")
    v = v.strip()
    if "\x00" in v or not (min_len <= len(v) <= max_len):
        raise HttpError(400, f"{name} must be {min_len}-{max_len} characters")
    return v


def int_(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise HttpError(400, f"{name} must be an integer")
    try:
        n = int(v)
    except ValueError:
        raise HttpError(400, f"{name} must be an integer")
    if not lo <= n <= hi:
        raise HttpError(400, f"{name} must be between {lo} and {hi}")
    return n


def bool_(v):
    return 1 if v in (True, 1, "1", "true", "on") else 0


def email(v, name="email"):
    v = str_(v, name, 3, 254)
    if not EMAIL_RE.match(v) or ".." in v:
        raise HttpError(400, f"{name} is not a valid e-mail address")
    return v


def parse_question(d):
    if not isinstance(d, dict):
        raise HttpError(400, "Invalid body")
    qtype = d.get("type")
    if qtype not in QTYPES:
        raise HttpError(400, "type must be one of " + ", ".join(QTYPES))
    out = {"text": str_(d.get("text"), "text", 3, 1000), "type": qtype,
           "category": str_(d.get("category") or "General", "category", 1, 60),
           "difficulty": d.get("difficulty") or "medium", "blank_answer": None, "options": []}
    if out["difficulty"] not in DIFFS:
        raise HttpError(400, "difficulty must be easy, medium or hard")
    if qtype == "fill":
        out["blank_answer"] = str_(d.get("blank_answer"), "blank_answer", 1, 200)
    elif qtype == "tf":
        if not isinstance(d.get("tf_answer"), bool):
            raise HttpError(400, "tf_answer must be true or false")
        out["options"] = [{"text": "True", "correct": d["tf_answer"]}, {"text": "False", "correct": not d["tf_answer"]}]
    else:
        opts = d.get("options")
        if not isinstance(opts, list) or not (2 <= len(opts) <= 8):
            raise HttpError(400, "Provide 2-8 options")
        for o in opts:
            if not isinstance(o, dict):
                raise HttpError(400, "Invalid option")
            out["options"].append({"text": str_(o.get("text"), "option text", 1, 300), "correct": bool(o.get("correct"))})
        n = sum(o["correct"] for o in out["options"])
        if qtype == "mcq" and n != 1:
            raise HttpError(400, "A single-answer question needs exactly one correct option")
        if qtype == "multi" and n < 1:
            raise HttpError(400, "Mark at least one correct option")
    return out


def parse_exam(d):
    if not isinstance(d, dict):
        raise HttpError(400, "Invalid body")
    qs = d.get("questions")
    if not isinstance(qs, list):
        raise HttpError(400, "questions must be a list")
    questions, seen = [], set()
    for item in qs:
        if isinstance(item, int):
            item = {"id": item}
        qid = int_(item.get("id") if isinstance(item, dict) else None, "question id", 1, 10**9)
        if qid in seen:
            raise HttpError(400, "Duplicate question")
        seen.add(qid)
        questions.append({"id": qid, "points": int_(item.get("points", 1), "points", 1, 100)})
    vis = d.get("notes_visibility", "after")
    if vis not in ("before", "after"):
        raise HttpError(400, "notes_visibility must be 'before' or 'after'")
    return {
        "title": str_(d.get("title"), "title", 3, 120),
        "topic": str_(d.get("topic") or "", "topic", 0, 120),
        "time_limit_min": int_(d.get("time_limit_min", 30), "time limit", 1, 600),
        "pass_mark": int_(d.get("pass_mark", 50), "pass mark", 0, 100),
        "attempt_limit": 1,  # UNIQUE(exam_id, student_id) caps this at 1
        "shuffle": bool_(d.get("shuffle", 1)), "reveal_answers": bool_(d.get("reveal_answers", 0)),
        "one_at_a_time": bool_(d.get("one_at_a_time", 1)), "send_parent": bool_(d.get("send_parent", 1)),
        "notes_visibility": vis, "questions": questions,
    }
