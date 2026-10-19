# ExamShield

Browser-based online exam platform: question bank, exam builder, live proctoring signals (SSE), PDF/CSV reports,
**result e-mail to parents** and **study-notes upload**. Pure Python 3.10+ standard library - no pip, no CDN.

## Quick start
```bash
python main.py            # first run: creates .env, DB, demo data; prints URL + QR
python -m unittest discover -s tests -t .      # 73 tests
```
* Student join: `http://<LAN-IP>:8080/?code=DEMO01` (scan the QR printed in the terminal)
* Examiner: `http://<LAN-IP>:8080/admin` - user `admin`, password printed once on first run (also in `.env`)
* Demo: exam `DEMO01` (15 cybersecurity questions), students `S001 Alice Demo`, `S002 Bob Demo`, `S003 Carol Demo` (any name + ID can join)

## Architecture
```
 Browser (client/*.html, js, css)  --JSON/SSE-->  server/app.py
   headers -> static -> rate limit -> session -> CSRF -> router (lib/router.py)
                                   |
        routes/auth  routes/admin  routes/student
                                   |
   services/attempts (start, answer, grade, finalize, sweep)   services/live (SSE hub)
   services/reports (PDF 1.4, ZIP, CSV)   services/notes (uploads)   services/mailer (parent e-mail)
                                   |
                db.py (sqlite3, tx())      lib/: qr, risk, shuffle, sessions, security, validate
```

## New features
**Result to parent.** On the join page a student may enter a parent/guardian name + e-mail (stated on screen, so it is
transparent). When the attempt is submitted - by the student, timer, strike limit or examiner closing the exam - the
server e-mails score, pass/fail, integrity summary counts and a PDF (answer review only if the exam reveals answers).
Per exam toggle `send_parent`. The examiner sees mail status per student, can fix the address, resend one, or
"E-mail all parents". With `SMTP_HOST` set it uses `smtplib` (STARTTLS); without it, mails are written to
`data/outbox/*.eml` (status `saved`). Mail failures never affect grading.

**Study notes.** Examiner uploads files for an exam/topic (pdf, txt, md, csv, png, jpg, docx, pptx; max `MAX_UPLOAD_MB`).
Type + magic-byte checked, stored under random names outside the web root, served as `attachment` + `nosniff`.
Visibility per exam: `before` (rules screen) or `after` (result screen). Never available during an attempt.

## Anti-cheat signals (honest disclaimer)
Browser signals are deterrents and evidence for a human examiner - **not proof**. A determined cheater can use a second
device. Do not treat risk levels as verdicts.

| Signal | Detection | Effect |
|---|---|---|
| Tab/window switch | `visibilitychange`, `blur` | Strike |
| Leaving fullscreen | `fullscreenchange` | Strike + overlay |
| Second tab | `BroadcastChannel` ping/pong | Strike + blocked tab |
| Session taken over | new login for running attempt | Strike + old session locked |
| Copy/paste/cut/right-click | blocked + reported | Flag (+1) |
| Ctrl+C/V/P/U/S, F12, DevTools combos | blocked + reported | Flag (+1) |
| Answer < `FAST_ANSWER_MS` after delivery | server | Flag (+1) |
| Same IP as another student | server | Flag (+1) |
| Similar wrong fill-in answer | server Levenshtein | Flag (+2) |

Weights: tab/fullscreen/second-tab 3, takeover 4. Risk: Low 0-4, Medium 5-11, High 12+ or any auto-submitted attempt.
`MAX_STRIKES` strikes auto-submit.

## Config (`.env`)
| Key | Default | Meaning |
|---|---|---|
| PORT / HOST | 8080 / 0.0.0.0 | listen address |
| BASE_URL | auto | used in QR codes |
| ADMIN_PASSWORD | generated | seeded admin password |
| DB_PATH | ./data/examshield.db | SQLite file (uploads/outbox live beside it) |
| RATE_LIMIT_API / LOGIN | 600 / 30 | requests per minute |
| FAST_ANSWER_MS | 800 | "too fast" threshold |
| MAX_STRIKES | 3 | auto-submit limit |
| MAX_UPLOAD_MB | 5 | notes file size cap |
| SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TLS | blank | parent e-mail delivery |

## Security notes
Correct answers never reach the browser before submission; the server owns the clock (`deadline_at`); grading only in
`finalize()`; `UNIQUE(exam_id, student_id)`; parameterised SQL only; 256-bit session tokens (SHA-256 stored), HttpOnly,
SameSite=Lax, regenerated on login; CSRF = SameSite + `X-Requested-With: ExamShield` + Origin/Host; strict CSP (no inline
script/style); DOM built with `textContent`, no `innerHTML`; CSV formula-injection safe; passwords PBKDF2-SHA256.

## Known limitations
* One attempt per student per exam (DB constraint); `attempt_limit` is capped at 1. Retakes need a new exam.
* A student re-logging in legitimately (crash) gets a takeover strike.
* Plain HTTP on LAN: put a TLS proxy in front for internet use and set `BASE_URL=https://...` (enables `Secure` cookie).
* QR encoder: byte mode, ECC L, versions 1-10 (up to 271 bytes). The unit test verifies format bits, Reed-Solomon and
  payload with an independent matrix reader; it was not scanned with a third-party library because pip/network is unavailable here.
* Entering a parent e-mail is self-declared; the examiner can correct it. Check local privacy rules (e.g. minors' data) before use.
* Single process, SQLite WAL: suited to classroom scale (tens to low hundreds of students).
