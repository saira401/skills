"""Demo data: admin account, 15-question cybersecurity bank, exam DEMO01, 3 demo students, sample notes."""
import time

from . import db
from .config import cfg
from .lib.validate import parse_question
from .routes.auth import create_admin

# (text, type, difficulty, payload)
BANK = [
    ("Which attack tricks users into revealing credentials via a fake login page?", "mcq", "easy", ["Phishing", "SQL injection", "Port scanning", "ARP poisoning"], [0]),
    ("What does HTTPS add on top of HTTP?", "mcq", "easy", ["TLS encryption", "Faster routing", "DNS caching", "Cookie compression"], [0]),
    ("Which of these is the strongest password policy?", "mcq", "easy", ["Long unique passphrase + password manager", "Same password everywhere", "Birthday + year", "Short but with symbols"], [0]),
    ("Which HTTP header best mitigates cross-site scripting by restricting script sources?", "mcq", "medium", ["Content-Security-Policy", "Accept-Language", "ETag", "Content-Length"], [0]),
    ("Which technique prevents SQL injection most reliably?", "mcq", "medium", ["Parameterised queries", "Hiding the database port", "Client-side validation only", "Longer table names"], [0]),
    ("What does the 'A' in the CIA triad stand for?", "mcq", "easy", ["Availability", "Authentication", "Authorisation", "Anonymity"], [0]),
    ("Which protocol is used to securely administer a remote server?", "mcq", "easy", ["SSH", "Telnet", "FTP", "TFTP"], [0]),
    ("Which hash function is considered broken for security use?", "mcq", "medium", ["MD5", "SHA-256", "SHA-512", "BLAKE2"], [0]),
    ("Select ALL multi-factor authentication factors.", "multi", "medium", ["Something you know", "Something you have", "Something you are", "Something you wrote down on a sticky note"], [0, 1, 2]),
    ("Select ALL examples of malware.", "multi", "easy", ["Ransomware", "Trojan", "Worm", "Firewall"], [0, 1, 2]),
    ("Select ALL good practices for securing a web session cookie.", "multi", "hard", ["HttpOnly flag", "SameSite attribute", "Secure flag on HTTPS", "Store the password inside it"], [0, 1, 2]),
    ("A firewall alone is enough to protect an organisation.", "tf", "easy", None, False),
    ("Public Wi-Fi without a VPN can expose unencrypted traffic to eavesdroppers.", "tf", "easy", None, True),
    ("Hashing is the same thing as encryption.", "tf", "medium", None, False),
    ("What is the name of the attack that floods a service with traffic to make it unavailable? (abbreviation)", "fill", "medium", None, "DDoS|DoS"),
]
NOTES = """Cybersecurity basics - study notes
==================================
Topic: core security concepts used in the DEMO01 exam.

1. CIA triad: Confidentiality, Integrity, Availability.
2. Phishing: deceptive messages that steal credentials. Check the sender and the URL.
3. Passwords: prefer long unique passphrases and a password manager; enable MFA.
4. SQL injection: use parameterised queries, never string concatenation.
5. XSS: output-encode user data and use a strict Content-Security-Policy.
6. Hashing is one-way, encryption is reversible with a key. MD5 and SHA-1 are broken for security use.
7. Cookies: HttpOnly, Secure and SameSite reduce theft and CSRF.
8. DoS/DDoS: overwhelm a service; mitigate with rate limiting and upstream filtering.
"""


def _insert_question(item):
    text, qtype, diff, a, b = item
    d = {"text": text, "type": qtype, "difficulty": diff, "category": "Cybersecurity"}
    if qtype == "fill":
        d["blank_answer"] = b
    elif qtype == "tf":
        d["tf_answer"] = b
    else:
        d["options"] = [{"text": t, "correct": i in b} for i, t in enumerate(a)]
    p = parse_question(d)
    qid = db.run("INSERT INTO questions(text, type, category, difficulty, blank_answer, created_at) VALUES (?,?,?,?,?,?)",
                 (p["text"], p["type"], p["category"], p["difficulty"], p["blank_answer"], int(time.time()))).lastrowid
    for i, o in enumerate(p["options"]):
        db.run("INSERT INTO question_options(question_id, text, is_correct, position) VALUES (?,?,?,?)", (qid, o["text"], int(o["correct"]), i))
    return qid


def run_seed():
    """Idempotent. Returns True if demo data was created."""
    if db.q("SELECT 1 FROM admins LIMIT 1", one=True):
        return False
    from .services import notes
    with db.tx():
        create_admin("admin", cfg.ADMIN_PASSWORD)
        ids = [_insert_question(i) for i in BANK]
        eid = db.run("INSERT INTO exams(code, title, topic, time_limit_min, pass_mark, shuffle, reveal_answers, one_at_a_time, "
                     "send_parent, notes_visibility, status, created_at) VALUES ('DEMO01','Cybersecurity Basics (demo)','Cybersecurity basics',"
                     "20,60,1,1,1,1,'after','open',?)", (int(time.time()),)).lastrowid
        for pos, qid in enumerate(ids):
            db.run("INSERT INTO exam_questions(exam_id, question_id, position, points) VALUES (?,?,?,1)", (eid, qid, pos))
        for code, name, pn, pe in (("S001", "Alice Demo", "Mr Demo", "parent.alice@example.com"),
                                   ("S002", "Bob Demo", "Mrs Demo", "parent.bob@example.com"),
                                   ("S003", "Carol Demo", None, None)):
            db.run("INSERT INTO students(student_code, name, parent_name, parent_email, created_at) VALUES (?,?,?,?,?)",
                   (code, name, pn, pe, int(time.time())))
        notes.save(eid, "cybersecurity-basics.txt", "Cybersecurity basics", NOTES.encode())
    return True
