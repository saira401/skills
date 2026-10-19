"""Hand-written PDF 1.4 builder, per-student report, bulk zip, formula-injection-safe CSV."""
import csv
import io
import re
import time
import zipfile

from .. import db
from . import attempts


# ---------- PDF 1.4 ----------
def _esc(s):
    s = s.encode("cp1252", "replace").decode("cp1252")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text, size, width=495):
    maxc = max(10, int(width / (size * 0.52)))
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            while len(word) > maxc:
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:maxc])
                word = word[maxc:]
            if len(line) + len(word) + (1 if line else 0) > maxc:
                out.append(line)
                line = word
            else:
                line = (line + " " + word) if line else word
        out.append(line)
    return out


def build_pdf(lines):
    """lines: list of (text, size, bold). Returns PDF 1.4 bytes with a valid xref table."""
    import zlib
    pages, cur, y = [], [], 792
    for text, size, bold in lines:
        for ln in _wrap(text, size):
            if y < 60:
                pages.append(cur)
                cur, y = [], 792
            cur.append(f"BT /{'F2' if bold else 'F1'} {size} Tf 50 {y} Td ({_esc(ln)}) Tj ET")
            y -= size * 1.4
    pages.append(cur)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for p in pages:
        page_no, content_no = len(objs) + 1, len(objs) + 2
        kids.append(f"{page_no} 0 R")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                    f"/Contents {content_no} 0 R >>".encode())
        data = zlib.compress("\n".join(p).encode("cp1252", "replace"))
        objs.append(f"<< /Length {len(data)} /Filter /FlateDecode >>\nstream\n".encode() + data + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def fmt_ts(ms):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ms / 1000)) if ms else "-"


def student_report(attempt_id, for_parent=False):
    """for_parent: summary + integrity counts (+ answer review only if the exam reveals answers)."""
    a = db.q("SELECT * FROM attempts WHERE id=?", (attempt_id,), one=True)
    s = attempts.summary(a)
    exam = db.q("SELECT * FROM exams WHERE id=?", (a["exam_id"],), one=True)
    L = [("ExamShield - Result Report", 20, True), (s["exam"], 14, True), ("", 8, False),
         (f"Student: {s['name']}  (ID {s['student_id']})", 11, False),
         (f"Started:   {fmt_ts(s['started_at'])}", 11, False), (f"Submitted: {fmt_ts(s['submitted_at'])}", 11, False),
         ("", 8, False)]
    if s["status"] == "submitted":
        L += [(f"Score: {s['score']:g} / {s['max_score']:g}   ({s['percent']:g}%)", 14, True),
              (f"Result: {'PASS' if s['passed'] else 'FAIL'} (pass mark {s['pass_mark']}%)", 12, True)]
    else:
        L.append(("Attempt still in progress", 12, True))
    if s["auto_submitted"]:
        L.append((f"Auto-submitted: {s['auto_reason']}", 11, False))
    L += [("", 8, False), ("Integrity summary", 13, True),
          (f"Risk level: {s['risk'].upper()} (score {s['risk_score']})", 11, False),
          (f"Strikes: {s['strikes']}   Focus-loss events: {s['focus_loss']}", 11, False),
          ("Signals are automated indicators from the browser, not proof of misconduct.", 9, False)]
    if not for_parent:
        L += [("", 8, False), ("Event timeline", 13, True)]
        evs = db.q("SELECT * FROM events WHERE attempt_id=? ORDER BY id", (attempt_id,))
        for e in evs or []:
            L.append((f"{fmt_ts(e['created_at'])}  {e['kind']}  {e['detail'] or ''}", 9, False))
        if not evs:
            L.append(("No events recorded.", 10, False))
    if (not for_parent or exam["reveal_answers"]) and s["status"] == "submitted":
        L += [("", 8, False), ("Answers", 13, True)]
        for i, r in enumerate(attempts.answer_rows(attempt_id), 1):
            L.append((f"{i}. {r['text']}", 10, True))
            L.append((f"   Answer: {r['given']}   [{'correct' if r['is_correct'] else 'wrong'}  {r['points']:g}/{r['max_points']}]", 10, False))
            if not r["is_correct"]:
                L.append((f"   Correct: {r['correct_answer']}", 10, False))
    return build_pdf(L)


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:60] or "file"


def bulk_zip(exam_id):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in db.q("SELECT a.id, s.name, s.student_code FROM attempts a JOIN students s ON s.id=a.student_id "
                      "WHERE a.exam_id=? ORDER BY s.name", (exam_id,)):
            z.writestr(f"{safe_name(r['student_code'])}_{safe_name(r['name'])}.pdf", student_report(r["id"]))
    return buf.getvalue()


# ---------- CSV ----------
def csv_cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def results_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["rank", "name", "student_id", "status", "score", "max_score", "percent", "passed", "risk", "risk_score",
            "strikes", "auto_submitted", "submitted_at", "parent_email", "parent_email_status"]
    w.writerow(cols)
    for r in rows:
        w.writerow([csv_cell(r.get(c)) for c in cols])
    return buf.getvalue().encode("utf-8-sig")
