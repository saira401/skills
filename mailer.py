"""Send each student's result (with a PDF summary) to the parent/guardian e-mail the student supplied.

* SMTP_HOST set  -> real delivery with smtplib (stdlib), STARTTLS when SMTP_TLS=1.
* SMTP_HOST blank -> message is written to data/outbox/*.eml and logged as 'saved' (nothing leaves the machine).
Every attempt is recorded in parent_emails so the examiner sees what happened and can resend.
"""
import re
import smtplib
import threading
import time
from email.message import EmailMessage

from .. import db
from ..config import cfg
from . import attempts, reports


def _build(attempt_id, to_addr):
    a = db.q("SELECT * FROM attempts WHERE id=?", (attempt_id,), one=True)
    s = attempts.summary(a)
    stu = db.q("SELECT * FROM students WHERE id=?", (a["student_id"],), one=True)
    msg = EmailMessage()
    msg["Subject"] = f"Exam result for {s['name']}: {s['exam']}"
    msg["From"] = cfg.SMTP_FROM or "ExamShield <examshield@localhost>"
    msg["To"] = to_addr
    greet = f"Dear {stu['parent_name']}," if stu["parent_name"] else "Dear parent/guardian,"
    lines = [greet, "", f"{s['name']} (student ID {s['student_id']}) has completed the exam \"{s['exam']}\".", "",
             f"Score:   {s['score']:g} / {s['max_score']:g} ({s['percent']:g}%)",
             f"Result:  {'PASS' if s['passed'] else 'FAIL'} (pass mark {s['pass_mark']}%)",
             f"Submitted: {reports.fmt_ts(s['submitted_at'])}"]
    if s["auto_submitted"]:
        why = {"time": "the time limit ran out", "strike limit": "the exam rules were broken repeatedly "
               "(e.g. leaving the exam window)", "exam closed": "the examiner closed the exam"}.get(s["auto_reason"], "an automatic rule")
        lines += ["", f"Note: the exam was submitted automatically because {why}."]
    if s["strikes"]:
        lines.append(f"The proctoring system recorded {s['strikes']} focus/rule warning(s). These are automated "
                     "indicators and not proof of misconduct.")
    lines += ["", "A PDF summary is attached.", "",
              "You are receiving this because the student provided your address when joining the exam. "
              "If this is a mistake, please ignore this message and contact the examiner."]
    msg.set_content("\n".join(lines))
    msg.add_attachment(reports.student_report(attempt_id, for_parent=True), maintype="application", subtype="pdf",
                       filename=f"result-{reports.safe_name(s['student_id'])}.pdf")
    return msg


def _deliver(email_id):
    row = db.q("SELECT * FROM parent_emails WHERE id=?", (email_id,), one=True)
    status, err = "failed", None
    try:
        msg = _build(row["attempt_id"], row["to_addr"])
        if cfg.SMTP_HOST:
            with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=20) as s:
                if cfg.SMTP_TLS:
                    s.starttls()
                if cfg.SMTP_USER:
                    s.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
                s.send_message(msg)
            status = "sent"
        else:
            cfg.outbox_dir.mkdir(parents=True, exist_ok=True)
            (cfg.outbox_dir / f"attempt{row['attempt_id']}-{email_id}.eml").write_bytes(bytes(msg))
            status, err = "saved", "SMTP not configured - saved to data/outbox/"
    except Exception as e:  # network/SMTP errors must never break grading
        err = re.sub(r"\s+", " ", str(e))[:200]
    db.run("UPDATE parent_emails SET status=?, error=?, sent_at=? WHERE id=?",
           (status, err, int(time.time()) if status in ("sent", "saved") else None, email_id))
    return status


def notify_parent(attempt_id, to=None, force=False):
    """Queue a result mail. Automatic sends happen once per attempt; force=True (examiner resend) always sends."""
    a = db.q("SELECT * FROM attempts WHERE id=?", (attempt_id,), one=True)
    stu = db.q("SELECT * FROM students WHERE id=?", (a["student_id"],), one=True)
    to = to or stu["parent_email"]
    if not to:
        if force:
            db.run("INSERT INTO parent_emails(attempt_id, to_addr, status, error, created_at) VALUES (?,?,?,?,?)",
                   (attempt_id, None, "skipped", "No parent e-mail on file", int(time.time())))
        return None
    if not force and db.q("SELECT 1 FROM parent_emails WHERE attempt_id=? AND status IN ('sent','saved','queued')",
                          (attempt_id,), one=True):
        return None
    eid = db.run("INSERT INTO parent_emails(attempt_id, to_addr, status, created_at) VALUES (?,?,?,?)",
                 (attempt_id, to, "queued", int(time.time()))).lastrowid
    if cfg.MAIL_SYNC:
        _deliver(eid)
    else:
        threading.Thread(target=_deliver, args=(eid,), daemon=True).start()
    return eid


def last_status(attempt_id):
    return db.q("SELECT to_addr, status, error, sent_at FROM parent_emails WHERE attempt_id=? ORDER BY id DESC LIMIT 1",
                (attempt_id,), one=True)
