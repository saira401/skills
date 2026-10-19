"""Integration + E2E tests against a real server on a random port with a temp database."""
import email
import email.policy
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import db, seed  # noqa: E402
from server.config import cfg  # noqa: E402
from server.app import APP, Handler, Server  # noqa: E402
from server.lib.security import RateLimiter  # noqa: E402
from server.services import attempts, live  # noqa: E402

PW = "test-admin-pw"


class Client:
    def __init__(self, port):
        self.port, self.cookies = port, {}

    def req(self, method, path, body=None, raw=None, headers=None, csrf=True):
        h = dict(headers or {})
        if csrf:
            h["X-Requested-With"] = "ExamShield"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        for sc in r.getheaders():
            if sc[0].lower() == "set-cookie":
                k, v = sc[1].split(";")[0].split("=", 1)
                if v:
                    self.cookies[k] = v
                else:
                    self.cookies.pop(k, None)
        c.close()
        ctype = r.getheader("Content-Type", "")
        js = json.loads(payload) if ctype.startswith("application/json") and payload else None
        return r.status, js, payload, r

    def j(self, method, path, body=None, **kw):
        s, js, _, _ = self.req(method, path, body, **kw)
        return s, js


def correct_response(qid, text_override=None):
    qu = db.q("SELECT * FROM questions WHERE id=?", (qid,), one=True)
    opts = db.q("SELECT id, is_correct FROM question_options WHERE question_id=?", (qid,))
    if qu["type"] == "fill":
        return {"text": text_override or qu["blank_answer"].split("|")[0]}
    good = [o["id"] for o in opts if o["is_correct"]]
    return {"choices": good} if qu["type"] == "multi" else {"choice": good[0]}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg.load(overrides={"DB_PATH": os.path.join(cls.tmp.name, "t.db"), "ADMIN_PASSWORD": PW, "PBKDF2_ITER": 1000,
                            "RATE_LIMIT_LOGIN": 1000, "RATE_LIMIT_API": 100000, "MAIL_SYNC": 1, "QUIET": 1, "FAST_ANSWER_MS": 300,
                            "MAX_UPLOAD_MB": 1})
        APP.limiter = RateLimiter()
        db.init()
        seed.run_seed()
        cls.server = Server(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.admin = Client(cls.port)
        assert cls.admin.j("POST", "/api/auth/admin/login", {"username": "admin", "password": PW})[0] == 200

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    # helpers
    def make_questions(self, n=3):
        ids = []
        for i in range(n):
            s, q = self.admin.j("POST", "/api/admin/questions", {"type": "mcq", "text": f"Pick the right one {i}", "category": "T",
                                "options": [{"text": "right", "correct": True}, {"text": "wrong", "correct": False}]})
            self.assertEqual(s, 201)
            ids.append(q["id"])
        return ids

    def make_exam(self, qids=None, **kw):
        qids = qids or self.make_questions()
        body = dict(title="Test exam", time_limit_min=10, pass_mark=50, shuffle=True, reveal_answers=True, one_at_a_time=True,
                    send_parent=True, notes_visibility="after", questions=qids)
        body.update(kw)
        s, e = self.admin.j("POST", "/api/admin/exams", body)
        self.assertEqual(s, 201, e)
        self.assertEqual(self.admin.j("POST", f"/api/admin/exams/{e['id']}/status", {"status": "open"})[0], 200)
        return e

    def student(self, exam, sid, name=None, **extra):
        c = Client(self.port)
        body = {"code": exam["code"], "name": name or f"Student {sid}", "student_id": sid}
        body.update(extra)
        s, js = c.j("POST", "/api/student/join", body)
        self.assertEqual(s, 200, js)
        return c

    def begin(self, c):
        self.assertEqual(c.j("POST", "/api/student/start", {})[0], 200)
        s, d = c.j("GET", "/api/student/questions")
        self.assertEqual(s, 200)
        return d

    def finish_all(self, c, wait=0.35):
        while True:
            s, d = c.j("GET", "/api/student/questions")
            if not d["questions"]:
                break
            time.sleep(wait)
            qid = d["questions"][0]["id"]
            self.assertEqual(c.j("POST", "/api/student/answer", {"question_id": qid, "response": correct_response(qid)})[0], 200)
        return c.j("POST", "/api/student/submit", {})


class TestAuthSecurity(Base):
    def test_admin_routes_need_login(self):
        self.assertEqual(Client(self.port).j("GET", "/api/admin/exams")[0], 401)

    def test_csrf_header_required(self):
        s, _ = self.admin.j("POST", "/api/admin/questions", {"type": "tf", "text": "abcd", "tf_answer": True}, csrf=False)
        self.assertEqual(s, 403)

    def test_cross_origin_blocked(self):
        s, _ = self.admin.j("POST", "/api/auth/logout", {}, headers={"Origin": "http://evil.example"})
        self.assertEqual(s, 403)

    def test_login_wrong_password(self):
        self.assertEqual(Client(self.port).j("POST", "/api/auth/admin/login", {"username": "admin", "password": "nope"})[0], 401)

    def test_cookie_flags_and_regeneration(self):
        c = Client(self.port)
        _, _, _, r1 = c.req("POST", "/api/auth/admin/login", {"username": "admin", "password": PW})
        sc = r1.getheader("Set-Cookie")
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Lax", sc)
        first = c.cookies["es_session"]
        self.assertGreaterEqual(len(first), 43)  # 256-bit urlsafe token
        c.j("POST", "/api/auth/admin/login", {"username": "admin", "password": PW})
        self.assertNotEqual(first, c.cookies["es_session"])
        self.assertEqual(db.q("SELECT 1 FROM sessions WHERE token_hash=?", (__import__("hashlib").sha256(first.encode()).hexdigest(),)), [])

    def test_security_headers_and_static(self):
        s, _, body, r = Client(self.port).req("GET", "/")
        self.assertEqual(s, 200)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn(b"<script>", body.replace(b"<script src", b""))
        self.assertEqual(Client(self.port).req("GET", "/../server/config.py")[0], 404)

    def test_no_innerhtml_in_client(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client", "js")
        for f in os.listdir(root):
            self.assertNotIn("innerHTML", open(os.path.join(root, f), encoding="utf-8").read(), f)

    def test_login_rate_limit(self):
        old = APP.limiter, cfg.RATE_LIMIT_LOGIN
        APP.limiter, cfg.RATE_LIMIT_LOGIN = RateLimiter(), 3
        try:
            codes = [Client(self.port).j("POST", "/api/auth/admin/login", {"username": "admin", "password": "x"})[0] for _ in range(5)]
        finally:
            APP.limiter, cfg.RATE_LIMIT_LOGIN = old
        self.assertEqual(codes[:3], [401] * 3)
        self.assertEqual(codes[3:], [429, 429])


class TestBankAndExams(Base):
    def test_question_crud(self):
        s, q = self.admin.j("POST", "/api/admin/questions", {"type": "multi", "text": "Select primes", "category": "Math", "difficulty": "hard",
                            "options": [{"text": "2", "correct": True}, {"text": "4", "correct": False}, {"text": "3", "correct": True}]})
        self.assertEqual(s, 201)
        s, q2 = self.admin.j("PUT", f"/api/admin/questions/{q['id']}", {"type": "fill", "text": "Capital of France?", "blank_answer": "Paris"})
        self.assertEqual((s, q2["type"], q2["options"]), (200, "fill", []))
        self.assertEqual(self.admin.j("DELETE", f"/api/admin/questions/{q['id']}")[0], 200)
        self.assertEqual(self.admin.j("GET", f"/api/admin/questions/{q['id']}")[0], 404)

    def test_question_validation(self):
        bad = {"type": "mcq", "text": "Two right?", "options": [{"text": "a", "correct": True}, {"text": "b", "correct": True}]}
        self.assertEqual(self.admin.j("POST", "/api/admin/questions", bad)[0], 400)
        self.assertEqual(self.admin.j("POST", "/api/admin/questions", {"type": "zzz", "text": "abcd"})[0], 400)

    def test_cannot_delete_question_used_in_exam(self):
        e = self.make_exam()
        qid = e["questions"][0]["id"]
        self.assertEqual(self.admin.j("DELETE", f"/api/admin/questions/{qid}")[0], 409)

    def test_exam_code_and_qr_png(self):
        e = self.make_exam()
        self.assertRegex(e["code"], r"^[A-Z0-9]{6}$")
        s, _, png, r = self.admin.req("GET", f"/api/admin/exams/{e['id']}/qr.png?download=1")
        self.assertEqual((s, png[:4]), (200, b"\x89PNG"))
        self.assertIn("attachment", r.getheader("Content-Disposition"))

    def test_demo_seed(self):
        self.assertEqual(db.q("SELECT COUNT(*) n FROM questions", one=True)["n"] >= 15, True)
        self.assertEqual(db.q("SELECT status FROM exams WHERE code='DEMO01'", one=True)["status"], "open")
        self.assertEqual(db.q("SELECT COUNT(*) n FROM students WHERE student_code IN ('S001','S002','S003')", one=True)["n"], 3)

    def test_join_unknown_and_draft(self):
        c = Client(self.port)
        self.assertEqual(c.j("POST", "/api/student/join", {"code": "ZZZZZZ", "name": "Al Bo", "student_id": "X1"})[0], 404)
        s, e = self.admin.j("POST", "/api/admin/exams", dict(title="Draft exam", questions=self.make_questions(1)))
        self.assertEqual(c.j("POST", "/api/student/join", {"code": e["code"], "name": "Al Bo", "student_id": "X1"})[0], 404)


class TestExamFlow(Base):
    def test_correct_answers_never_leak(self):
        e = self.make_exam()
        c = self.student(e, "LEAK1")
        pre = json.dumps(c.j("GET", "/api/student/state")[1])
        d = self.begin(c)
        blob = json.dumps(d) + pre
        for forbidden in ("is_correct", "correct", "blank_answer"):
            self.assertNotIn(forbidden, blob)
        qid = d["questions"][0]["id"]
        time.sleep(0.35)
        s, resp = c.j("POST", "/api/student/answer", {"question_id": qid, "response": correct_response(qid)})
        self.assertNotIn("correct", json.dumps(resp))
        self.assertEqual(c.j("GET", "/api/student/result")[0], 409)  # no result/review before submission

    def test_one_at_a_time_and_no_skipping(self):
        e = self.make_exam()
        c = self.student(e, "ONE1")
        d = self.begin(c)
        self.assertEqual(len(d["questions"]), 1)
        other = [r["question_id"] for r in db.q("SELECT question_id FROM exam_questions WHERE exam_id=?", (e["id"],))
                 if r["question_id"] != d["questions"][0]["id"]][0]
        s, js = c.j("POST", "/api/student/answer", {"question_id": other, "response": correct_response(other)})
        self.assertEqual((s, js["code"]), (409, "not_delivered"))

    def test_late_answer_rejected(self):
        e = self.make_exam()
        c = self.student(e, "LATE1")
        d = self.begin(c)
        qid = d["questions"][0]["id"]
        aid = db.q("SELECT id FROM attempts WHERE exam_id=?", (e["id"],), one=True)["id"]
        db.run("UPDATE attempts SET deadline_at=? WHERE id=?", (attempts.now_ms() - 5000, aid))
        s, js = c.j("POST", "/api/student/answer", {"question_id": qid, "response": correct_response(qid)})
        self.assertEqual((s, js["code"]), (409, "time_up"))
        row = db.q("SELECT response FROM answers WHERE attempt_id=? AND question_id=?", (aid, qid), one=True)
        self.assertIsNone(row["response"])
        a = db.q("SELECT * FROM attempts WHERE id=?", (aid,), one=True)
        self.assertEqual((a["status"], a["auto_submitted"], a["auto_reason"]), ("submitted", 1, "time"))

    def test_double_attempt_rejected_at_db_level(self):
        e = self.make_exam()
        c = self.student(e, "DUP1")
        self.begin(c)
        a = db.q("SELECT * FROM attempts WHERE exam_id=?", (e["id"],), one=True)
        with self.assertRaises(sqlite3.IntegrityError):
            db.run("INSERT INTO attempts(exam_id, student_id, started_at, deadline_at, seed) VALUES (?,?,?,?,?)",
                   (a["exam_id"], a["student_id"], 1, 2, "x"))
        self.assertEqual(c.j("POST", "/api/student/start", {})[0], 409)

    def test_grading_server_side_ignores_client_score(self):
        e = self.make_exam()
        c = self.student(e, "GRADE1")
        self.begin(c)
        s, r = c.j("POST", "/api/student/submit", {"score": 100, "percent": 100, "passed": True})
        self.assertEqual((s, r["score"], r["passed"]), (200, 0, False))

    def test_perfect_score_passes_and_review(self):
        e = self.make_exam()
        c = self.student(e, "PERF1")
        self.begin(c)
        s, r = self.finish_all(c)
        self.assertEqual((r["score"], r["max_score"], r["percent"], r["passed"]), (3, 3, 100, True))
        self.assertEqual(len(r["review"]), 3)

    def test_reveal_off_hides_review(self):
        e = self.make_exam(reveal_answers=False)
        c = self.student(e, "NOREV1")
        self.begin(c)
        _, r = self.finish_all(c, wait=0.35)
        self.assertIsNone(r["review"])

    def test_all_at_once_mode_and_multi_fill_grading(self):
        _, m = self.admin.j("POST", "/api/admin/questions", {"type": "multi", "text": "Select all even", "options": [
            {"text": "2", "correct": True}, {"text": "3", "correct": False}, {"text": "4", "correct": True}]})
        _, f = self.admin.j("POST", "/api/admin/questions", {"type": "fill", "text": "Capital of France", "blank_answer": "Paris|paris france"})
        e = self.make_exam([m["id"], {"id": f["id"], "points": 2}], one_at_a_time=False, shuffle=False)
        c = self.student(e, "ALL1")
        d = self.begin(c)
        self.assertEqual(len(d["questions"]), 2)
        time.sleep(0.35)
        c.j("POST", "/api/student/answer", {"question_id": m["id"], "response": correct_response(m["id"])})
        time.sleep(0.35)
        c.j("POST", "/api/student/answer", {"question_id": f["id"], "response": {"text": "  PARIS "}})
        _, r = c.j("POST", "/api/student/submit", {})
        self.assertEqual((r["score"], r["max_score"]), (3, 3))

    def test_fast_answer_flag(self):
        e = self.make_exam()
        c = self.student(e, "FAST1")
        d = self.begin(c)
        qid = d["questions"][0]["id"]
        c.j("POST", "/api/student/answer", {"question_id": qid, "response": correct_response(qid)})
        self.assertEqual(db.q("SELECT COUNT(*) n FROM events WHERE kind='fast_answer'", one=True)["n"] >= 1, True)

    def test_clipboard_and_shortcut_flags(self):
        e = self.make_exam()
        c = self.student(e, "CLIP1")
        self.begin(c)
        for k in ("copy", "paste", "contextmenu", "shortcut"):
            self.assertEqual(c.j("POST", "/api/student/event", {"kind": k})[1]["strikes"], 0)
        self.assertEqual(c.j("POST", "/api/student/event", {"kind": "hack"})[0], 400)
        self.assertEqual(c.j("POST", "/api/student/event", {"kind": "session_takeover"})[0], 400)  # server-only kind

    def test_same_ip_flag(self):
        e = self.make_exam()
        self.begin(self.student(e, "IP1"))
        self.begin(self.student(e, "IP2"))
        self.assertEqual(db.q("SELECT COUNT(*) n FROM events e JOIN attempts a ON a.id=e.attempt_id WHERE a.exam_id=? AND e.kind='same_ip'",
                              (e["id"],), one=True)["n"], 1)

    def test_fill_similarity_flag(self):
        _, f = self.admin.j("POST", "/api/admin/questions", {"type": "fill", "text": "Name the protocol used for secure shell", "blank_answer": "ssh"})
        e = self.make_exam([f["id"]], one_at_a_time=False)
        for sid in ("SIM1", "SIM2"):
            c = self.student(e, sid)
            self.begin(c)
            time.sleep(0.35)
            c.j("POST", "/api/student/answer", {"question_id": f["id"], "response": {"text": "secure sell protocol"}})
        self.assertEqual(db.q("SELECT COUNT(*) n FROM events e JOIN attempts a ON a.id=e.attempt_id WHERE a.exam_id=? AND e.kind='similar_answer'",
                              (e["id"],), one=True)["n"], 1)

    def test_session_takeover_locks_old_session(self):
        e = self.make_exam()
        old = self.student(e, "TAKE1", "Tina Take")
        self.begin(old)
        new = self.student(e, "TAKE1", "Tina Take")
        s, js = old.j("GET", "/api/student/questions")
        self.assertEqual((s, js["code"]), (409, "session_replaced"))
        self.assertEqual(old.j("GET", "/api/student/state")[1]["state"], "replaced")
        self.assertEqual(new.j("GET", "/api/student/questions")[0], 200)
        self.assertEqual(db.q("SELECT COUNT(*) n FROM events WHERE kind='session_takeover'", one=True)["n"] >= 1, True)

    def test_name_must_match_student_id(self):
        e = self.make_exam()
        self.student(e, "NAME1", "Real Name")
        s, _ = Client(self.port).j("POST", "/api/student/join", {"code": e["code"], "name": "Someone Else", "student_id": "NAME1"})
        self.assertEqual(s, 403)

    def test_sweep_auto_submits_expired(self):
        e = self.make_exam()
        c = self.student(e, "SWEEP1")
        self.begin(c)
        db.run("UPDATE attempts SET deadline_at=? WHERE exam_id=?", (attempts.now_ms() - 1000, e["id"]))
        attempts.sweep()
        a = db.q("SELECT * FROM attempts WHERE exam_id=?", (e["id"],), one=True)
        self.assertEqual((a["status"], a["auto_reason"]), ("submitted", "time"))

    def test_closing_exam_submits_running_attempts(self):
        e = self.make_exam()
        c = self.student(e, "CLOSE1")
        self.begin(c)
        self.admin.j("POST", f"/api/admin/exams/{e['id']}/status", {"status": "closed"})
        self.assertEqual(c.j("GET", "/api/student/result")[1]["auto_reason"], "exam closed")
        self.assertEqual(Client(self.port).j("POST", "/api/student/join", {"code": e["code"], "name": "New One", "student_id": "NEW9"})[0], 403)


class TestReportsLive(Base):
    n = 0

    def setUp(self):
        TestReportsLive.n += 1
        self.sid = f"REP{TestReportsLive.n}"
        self.e = self.make_exam()
        c = self.student(self.e, self.sid, "Rita Report")
        self.begin(c)
        self.finish_all(c)

    def test_pdf_export_valid(self):
        aid = db.q("SELECT id FROM attempts WHERE exam_id=?", (self.e["id"],), one=True)["id"]
        s, _, pdf, r = self.admin.req("GET", f"/api/admin/attempts/{aid}/pdf")
        self.assertEqual(s, 200)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"xref", pdf)
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_bulk_zip(self):
        s, _, z, _ = self.admin.req("GET", f"/api/admin/exams/{self.e['id']}/pdf.zip")
        names = zipfile.ZipFile(io.BytesIO(z)).namelist()
        self.assertEqual((s, len(names)), (200, 1))
        self.assertTrue(names[0].endswith(".pdf"))

    def test_csv_injection_safe(self):
        db.run("UPDATE students SET name='=cmd|calc' WHERE student_code=?", (self.sid,))
        s, _, body, _ = self.admin.req("GET", f"/api/admin/exams/{self.e['id']}/results.csv")
        text = body.decode("utf-8-sig")
        self.assertIn("'=cmd|calc", text)
        self.assertNotIn(",=cmd", text)

    def test_results_ranked(self):
        s, r = self.admin.j("GET", f"/api/admin/exams/{self.e['id']}/results")
        self.assertEqual((s, r["rows"][0]["rank"], r["rows"][0]["risk"]), (200, 1, "low"))

    def test_sse_snapshot(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", f"/api/admin/exams/{self.e['id']}/live", headers={"Cookie": f"es_session={self.admin.cookies['es_session']}"})
        r = c.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        data = b""
        while b"data: " not in data or not data.endswith(b"\n\n"):
            data += r.fp.readline()
        snap = json.loads(data.split(b"data: ", 1)[1])
        self.assertEqual(snap["attempts"][0]["name"], "Rita Report")
        c.close()


class TestParentEmail(Base):
    def outbox_msgs(self):
        d = cfg.outbox_dir
        return [email.message_from_bytes((d / f).read_bytes(), policy=email.policy.default) for f in sorted(os.listdir(d))] if d.exists() else []

    def test_result_emailed_to_parent_with_pdf(self):
        e = self.make_exam()
        c = self.student(e, "PAR1", "Pia Parent", parent_email="mum@example.com", parent_name="Mrs Parent")
        self.begin(c)
        _, r = self.finish_all(c)
        self.assertEqual(r["parent_email"]["status"], "saved")
        self.assertEqual(r["parent_email"]["to"], "m***@example.com")
        msg = [m for m in self.outbox_msgs() if m["To"] == "mum@example.com"][-1]
        self.assertIn("Pia Parent", msg["Subject"])
        body = msg.get_body(("plain",)).get_content()
        self.assertIn("Dear Mrs Parent", body)
        self.assertIn("100%", body)
        pdf = [p for p in msg.iter_attachments()][0].get_payload(decode=True)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))

    def test_invalid_parent_email_rejected(self):
        e = self.make_exam()
        s, _ = Client(self.port).j("POST", "/api/student/join", {"code": e["code"], "name": "Bad Mail", "student_id": "BM1", "parent_email": "x@y\r\nBcc: z@z.com"})
        self.assertEqual(s, 400)

    def test_no_parent_email_no_mail(self):
        e = self.make_exam()
        c = self.student(e, "NOPAR1")
        self.begin(c)
        _, r = self.finish_all(c)
        self.assertIsNone(r["parent_email"])
        aid = db.q("SELECT id FROM attempts WHERE exam_id=?", (e["id"],), one=True)["id"]
        self.assertEqual(db.q("SELECT COUNT(*) n FROM parent_emails WHERE attempt_id=?", (aid,), one=True)["n"], 0)

    def test_send_parent_toggle_off(self):
        e = self.make_exam(send_parent=False)
        c = self.student(e, "OFF1", parent_email="off@example.com")
        self.begin(c)
        _, r = self.finish_all(c)
        self.assertIsNone(r["parent_email"])

    def test_examiner_resend_and_update_address(self):
        e = self.make_exam()
        c = self.student(e, "RES1", "Rex Resend")
        self.begin(c)
        self.finish_all(c)
        aid = db.q("SELECT id FROM attempts WHERE exam_id=?", (e["id"],), one=True)["id"]
        s, js = self.admin.j("POST", f"/api/admin/attempts/{aid}/send-parent", {"email": "dad@example.com"})
        self.assertEqual((s, js["status"]["status"], js["status"]["to_addr"]), (200, "saved", "dad@example.com"))
        self.assertEqual(self.admin.j("POST", f"/api/admin/attempts/{aid}/send-parent", {"email": "bad"})[0], 400)
        det = self.admin.j("GET", f"/api/admin/attempts/{aid}")[1]
        self.assertEqual(det["parent"]["parent_email"], "dad@example.com")

    def test_smtp_failure_never_breaks_grading(self):
        old = cfg.SMTP_HOST, cfg.SMTP_PORT
        cfg.SMTP_HOST, cfg.SMTP_PORT = "127.0.0.1", 1  # nothing listens here
        try:
            e = self.make_exam()
            c = self.student(e, "FAILM1", parent_email="f@example.com")
            self.begin(c)
            s, r = self.finish_all(c)
        finally:
            cfg.SMTP_HOST, cfg.SMTP_PORT = old
        self.assertEqual((s, r["passed"], r["parent_email"]["status"]), (200, True, "failed"))

    def test_send_all_parents(self):
        e = self.make_exam(send_parent=False)
        c = self.student(e, "ALLP1", parent_email="all@example.com")
        self.begin(c)
        self.finish_all(c)
        s, js = self.admin.j("POST", f"/api/admin/exams/{e['id']}/send-parents", {})
        self.assertEqual((s, js["queued"]), (200, 1))


class TestNotes(Base):
    def upload(self, eid, name, data, client=None):
        return (client or self.admin).req("POST", f"/api/admin/exams/{eid}/notes", raw=data,
                                          headers={"X-Filename": name, "X-Topic": "Topic%20A", "Content-Type": "application/octet-stream"})

    def test_upload_list_download_delete(self):
        e = self.make_exam()
        s, js, _, _ = self.upload(e["id"], "My%20notes.txt", b"hello notes")
        self.assertEqual(s, 201)
        notes = self.admin.j("GET", f"/api/admin/exams/{e['id']}/notes")[1]["notes"]
        self.assertEqual((notes[0]["filename"], notes[0]["topic"]), ("My notes.txt", "Topic A"))
        s, _, data, r = self.admin.req("GET", f"/api/admin/notes/{js['id']}/download")
        self.assertEqual(data, b"hello notes")
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.admin.j("DELETE", f"/api/admin/notes/{js['id']}")[0], 200)
        self.assertEqual(self.admin.j("GET", f"/api/admin/exams/{e['id']}/notes")[1]["notes"], [])

    def test_upload_validation(self):
        e = self.make_exam()
        self.assertEqual(self.upload(e["id"], "evil.exe", b"MZ")[0], 400)
        self.assertEqual(self.upload(e["id"], "fake.pdf", b"not a pdf")[0], 400)
        self.assertEqual(self.upload(e["id"], "big.txt", b"x" * (1024 * 1024 + 10))[0], 413)
        self.assertEqual(self.upload(e["id"], "..%2F..%2Fx.txt", b"ok")[0], 201)
        stored = [p for p in os.listdir(cfg.upload_dir) if p.endswith(".txt")]
        self.assertTrue(all("/" not in p and ".." not in p for p in stored))

    def test_students_cannot_upload_or_use_admin_download(self):
        e = self.make_exam()
        c = self.student(e, "NOUP1")
        self.assertEqual(self.upload(e["id"], "a.txt", b"x", client=c)[0], 401)
        self.assertEqual(c.j("GET", "/api/admin/notes/1/download")[0], 401)

    def test_visibility_after_vs_before_and_never_during_exam(self):
        e_after = self.make_exam(notes_visibility="after")
        e_before = self.make_exam(notes_visibility="before")
        ids = {}
        for ex in (e_after, e_before):
            ids[ex["id"]] = self.upload(ex["id"], "n.txt", b"study me")[1]["id"]
        c1, c2 = self.student(e_after, "NV1"), self.student(e_before, "NV2")
        self.assertEqual(c1.j("GET", "/api/student/state")[1]["notes"], [])
        self.assertEqual(len(c2.j("GET", "/api/student/state")[1]["notes"]), 1)
        self.assertEqual(c1.j("GET", f"/api/student/notes/{ids[e_after['id']]}")[0], 403)
        s, _, data, _ = c2.req("GET", f"/api/student/notes/{ids[e_before['id']]}")
        self.assertEqual((s, data), (200, b"study me"))
        self.begin(c1)
        self.begin(c2)
        self.assertEqual(c2.j("GET", f"/api/student/notes/{ids[e_before['id']]}")[0], 403)  # blocked mid-exam
        _, r = self.finish_all(c1)
        self.assertEqual(len(r["notes"]), 1)
        self.assertEqual(c1.req("GET", f"/api/student/notes/{ids[e_after['id']]}")[2], b"study me")

    def test_student_cannot_read_other_exams_notes(self):
        e1, e2 = self.make_exam(notes_visibility="before"), self.make_exam(notes_visibility="before")
        nid = self.upload(e2["id"], "secret.txt", b"other")[1]["id"]
        c = self.student(e1, "XN1")
        self.assertEqual(c.j("GET", f"/api/student/notes/{nid}")[0], 404)


class TestE2E(Base):
    def test_full_exam_honest_vs_cheater(self):
        # examiner creates exam
        qids = self.make_questions(3)
        e = self.make_exam(qids, title="E2E exam", notes_visibility="after")
        self.upload = None
        # student 1: honest, parent address provided
        s1 = self.student(e, "E2E1", "Honest Hannah", parent_email="hannah.parent@example.com")
        self.begin(s1)
        _, r1 = self.finish_all(s1)
        self.assertEqual((r1["passed"], r1["percent"], r1["auto_submitted"]), (True, 100, False))
        # student 2: switches tabs 3x -> auto-submitted
        s2 = self.student(e, "E2E2", "Cheat Charlie", parent_email="charlie.parent@example.com")
        self.begin(s2)
        for i in range(3):
            s, ev = s2.j("POST", "/api/student/event", {"kind": "tab_switch", "detail": "visibilitychange"})
        self.assertEqual((ev["strikes"], ev["submitted"]), (3, True))
        self.assertEqual(s2.j("POST", "/api/student/event", {"kind": "tab_switch"})[0], 409)
        s, res = s2.j("GET", "/api/student/result")
        self.assertEqual((res["auto_submitted"], res["auto_reason"]), (True, "strike limit"))
        # results page shows correct risk levels
        rows = {r["student_id"]: r for r in self.admin.j("GET", f"/api/admin/exams/{e['id']}/results")[1]["rows"]}
        self.assertEqual(rows["E2E1"]["risk"], "low")
        self.assertEqual(rows["E2E2"]["risk"], "high")
        self.assertEqual((rows["E2E2"]["auto_submitted"], rows["E2E2"]["strikes"]), (True, 3))
        self.assertEqual(rows["E2E1"]["rank"], 1)
        # detail has cheating timeline
        aid = rows["E2E2"]["attempt_id"]
        det = self.admin.j("GET", f"/api/admin/attempts/{aid}")[1]
        kinds = [x["kind"] for x in det["events"]]
        self.assertEqual(kinds.count("tab_switch"), 3)
        self.assertIn("auto_submit", kinds)
        # both parents were mailed
        self.assertEqual({rows[k]["parent_email_status"] for k in rows}, {"saved"})
        # live dashboard snapshot agrees
        snap = live.snapshot(e["id"])
        self.assertEqual({a["student_id"]: a["risk"] for a in snap["attempts"]}, {"E2E1": "low", "E2E2": "high"})


if __name__ == "__main__":
    unittest.main()
