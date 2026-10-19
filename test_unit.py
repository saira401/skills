"""Unit tests: shuffle, risk, QR (with an independent matrix decoder), PNG, PDF bytes, CSV, validators, router, notes."""
import os
import re
import struct
import sys
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.config import cfg  # noqa: E402
from server.lib import qr, risk, shuffle, validate as V  # noqa: E402
from server.lib.router import Router, HttpError  # noqa: E402
from server.lib.security import RateLimiter, csrf_check  # noqa: E402
from server.services import notes, reports  # noqa: E402


def decode_qr(m):
    """Independent check: read format bits, unmask, de-interleave, verify Reed-Solomon, return payload bytes."""
    size = len(m)
    ver = (size - 17) // 4
    bits = 0
    for i in range(6):
        bits |= m[i][8] << i
    bits |= m[7][8] << 6
    bits |= m[8][8] << 7
    bits |= m[8][7] << 8
    for i in range(9, 15):
        bits |= m[8][14 - i] << i
    fmt = bits ^ 0x5412
    assert (fmt >> 13) == qr.FORMAT_L, "ECC level bits wrong"
    mask = (fmt >> 10) & 7
    assert qr.format_bits(mask) == bits, "format BCH mismatch"
    func = [[False] * size for _ in range(size)]

    def mark(x0, y0, w, h):
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                if 0 <= x < size and 0 <= y < size:
                    func[y][x] = True
    mark(0, 0, 9, 9); mark(size - 8, 0, 8, 9); mark(0, size - 8, 9, 8)
    for i in range(size):
        func[6][i] = func[i][6] = True
    pos = qr.ALIGN[ver]
    for i, py in enumerate(pos):
        for j, px in enumerate(pos):
            if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                continue
            mark(px - 2, py - 2, 5, 5)
    if ver >= 7:
        mark(size - 11, 0, 3, 6); mark(0, size - 11, 6, 3)
    bitlist = []
    for right in range(size - 1, 0, -2):
        if right <= 6:
            right -= 1
        up = ((right + 1) & 2) == 0
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if up else vert
                if not func[y][x]:
                    bitlist.append(int(m[y][x]) ^ int(qr.MASKS[mask](x, y)))
    ecl, groups = qr.EC_L[ver]
    total = sum(c * (d + ecl) for c, d in groups)
    cws = [int("".join(map(str, bitlist[i * 8:i * 8 + 8])), 2) for i in range(total)]
    blocks_d = [d for c, d in groups for _ in range(c)]
    nb = len(blocks_d)
    data_cw = [[] for _ in range(nb)]
    p = 0
    for i in range(max(blocks_d)):
        for b in range(nb):
            if i < blocks_d[b]:
                data_cw[b].append(cws[p]); p += 1
    ecs = [[] for _ in range(nb)]
    for i in range(ecl):
        for b in range(nb):
            ecs[b].append(cws[p]); p += 1
    div = qr.rs_divisor(ecl)
    for b in range(nb):
        assert qr.rs_remainder(data_cw[b], div) == ecs[b], "Reed-Solomon check failed"
    stream = "".join(format(c, "08b") for blk in data_cw for c in blk)
    assert stream[:4] == "0100", "not byte mode"
    cc = 8 if ver < 10 else 16
    length = int(stream[4:4 + cc], 2)
    body = stream[4 + cc:4 + cc + 8 * length]
    return bytes(int(body[i:i + 8], 2) for i in range(0, len(body), 8))


class TestShuffle(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(shuffle.seeded_shuffle(range(20), "s1"), shuffle.seeded_shuffle(range(20), "s1"))

    def test_permutation_and_seed_changes_order(self):
        a = shuffle.seeded_shuffle(range(30), "a")
        self.assertEqual(sorted(a), list(range(30)))
        self.assertNotEqual(a, shuffle.seeded_shuffle(range(30), "b"))
        self.assertNotEqual(a, list(range(30)))


class TestRisk(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(risk.compute_risk([])["level"], "low")
        self.assertEqual(risk.compute_risk(["tab_switch", "copy"])["level"], "low")            # 4
        self.assertEqual(risk.compute_risk(["tab_switch", "copy", "copy"])["level"], "medium")  # 5
        self.assertEqual(risk.compute_risk(["tab_switch"] * 4)["level"], "high")                # 12

    def test_auto_submit_is_high(self):
        self.assertEqual(risk.compute_risk([], auto_submitted=True)["level"], "high")

    def test_strikes_and_focus(self):
        r = risk.compute_risk(["tab_switch", "fullscreen_exit", "copy", "session_takeover"])
        self.assertEqual((r["strikes"], r["focus_loss"]), (3, 2))

    def test_levenshtein(self):
        self.assertEqual(risk.levenshtein("kitten", "sitting"), 3)
        self.assertEqual(risk.levenshtein("", "abc"), 3)
        self.assertEqual(risk.levenshtein("same", "same"), 0)


class TestQR(unittest.TestCase):
    def test_short_url_decodes(self):
        url = b"http://192.168.1.20:8080/?code=DEMO01"
        self.assertEqual(decode_qr(qr.make(url)), url)

    def test_all_versions_decode(self):
        for n in (5, 30, 60, 90, 120, 160, 200, 250):
            data = bytes((i * 7 + 3) % 256 for i in range(n))
            m = qr.make(data)
            self.assertEqual(decode_qr(m), data, f"{n} bytes (v{(len(m) - 17) // 4})")

    def test_too_long(self):
        with self.assertRaises(ValueError):
            qr.make(b"x" * 300)

    def test_finder_patterns(self):
        m = qr.make("hello")
        n = len(m)
        for (x, y) in ((0, 0), (n - 7, 0), (0, n - 7)):
            self.assertTrue(all(m[y][x + i] for i in range(7)) and all(m[y + 6][x + i] for i in range(7)))

    def test_png_valid(self):
        png = qr.png(qr.make("hi"), scale=4)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        pos, idat = 8, b""
        while pos < len(png):
            ln, tag = struct.unpack(">I4s", png[pos:pos + 8])
            body = png[pos + 8:pos + 8 + ln]
            crc = struct.unpack(">I", png[pos + 8 + ln:pos + 12 + ln])[0]
            self.assertEqual(crc, zlib.crc32(tag + body) & 0xFFFFFFFF)
            if tag == b"IDAT":
                idat += body
            pos += 12 + ln
        w = struct.unpack(">I", png[16:20])[0]
        self.assertEqual(len(zlib.decompress(idat)), w * (((w + 7) // 8) + 1))


class TestPDF(unittest.TestCase):
    def test_header_xref_trailer(self):
        pdf = reports.build_pdf([("Hello (world) \\ test", 12, True)] * 120)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        sx = int(re.search(rb"startxref\n(\d+)\n", pdf).group(1))
        self.assertEqual(pdf[sx:sx + 4], b"xref")
        count = int(re.search(rb"xref\n0 (\d+)\n", pdf).group(1))
        entries = re.findall(rb"(\d{10}) (\d{5}) ([nf]) \n", pdf[sx:])
        self.assertEqual(len(entries), count)
        for i, (off, _, kind) in enumerate(entries):
            if kind == b"n":
                self.assertTrue(pdf[int(off):].startswith(f"{i} 0 obj".encode()), f"object {i} offset wrong")

    def test_multi_page_and_flate(self):
        pdf = reports.build_pdf([("line", 12, False)] * 200)
        self.assertGreater(pdf.count(b"/Type /Page "), 1)
        self.assertIn(b"/FlateDecode", pdf)

    def test_unicode_does_not_crash(self):
        self.assertTrue(reports.build_pdf([("Zoë – 日本語 test", 12, False)]).startswith(b"%PDF"))


class TestCSV(unittest.TestCase):
    def test_formula_injection_neutralised(self):
        for bad in ("=SUM(A1)", "+1", "-2", "@cmd", "\tx"):
            self.assertTrue(reports.csv_cell(bad).startswith("'"))
        self.assertEqual(reports.csv_cell("Alice"), "Alice")
        out = reports.results_csv([{"name": "=HYPERLINK(\"x\")", "rank": 1}]).decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", out)


class TestValidate(unittest.TestCase):
    def test_email(self):
        self.assertEqual(V.email("a.b@example.com"), "a.b@example.com")
        for bad in ("nope", "a@b", "a@@b.com", "a b@c.com", "a@b.com\r\nBcc: x@y.com"):
            with self.assertRaises(HttpError):
                V.email(bad)

    def test_int_str(self):
        self.assertEqual(V.int_("5", "n", 1, 10), 5)
        with self.assertRaises(HttpError):
            V.int_(True, "n", 0, 1)
        with self.assertRaises(HttpError):
            V.str_("x", "n", 2, 5)

    def test_parse_question_rules(self):
        with self.assertRaises(HttpError):
            V.parse_question({"type": "mcq", "text": "abcd", "options": [{"text": "a", "correct": True}, {"text": "b", "correct": True}]})
        q = V.parse_question({"type": "tf", "text": "abcd", "tf_answer": False})
        self.assertEqual([o["correct"] for o in q["options"]], [False, True])

    def test_parse_exam(self):
        e = V.parse_exam({"title": "Exam one", "questions": [1, {"id": 2, "points": 3}], "attempt_limit": 9})
        self.assertEqual(e["attempt_limit"], 1)
        self.assertEqual(e["questions"][1]["points"], 3)
        with self.assertRaises(HttpError):
            V.parse_exam({"title": "Exam one", "questions": [1, 1]})


class TestRouterSecurity(unittest.TestCase):
    def test_router_params_404_405(self):
        r = Router()
        r.get("/a/<int:id>")(lambda req: {"id": req.params["id"]})

        class Req:
            method, path, session, params = "GET", "/a/42", None, {}
        self.assertEqual(r.dispatch(Req)["id"], 42)
        Req.path = "/nope"
        with self.assertRaises(HttpError) as c:
            r.dispatch(Req)
        self.assertEqual(c.exception.status, 404)
        Req.path, Req.method = "/a/1", "POST"
        with self.assertRaises(HttpError) as c:
            r.dispatch(Req)
        self.assertEqual(c.exception.status, 405)

    def test_rate_limiter(self):
        rl = RateLimiter()
        self.assertTrue(all(rl.allow("k", 3) for _ in range(3)))
        self.assertFalse(rl.allow("k", 3))
        self.assertTrue(rl.allow("other", 3))

    def test_csrf(self):
        class Req:
            method = "POST"
            headers = {"X-Requested-With": "ExamShield", "Host": "h:1"}
        csrf_check(Req)
        Req.headers = {"Host": "h:1"}
        with self.assertRaises(HttpError):
            csrf_check(Req)
        Req.headers = {"X-Requested-With": "ExamShield", "Host": "h:1", "Origin": "http://evil.com"}
        with self.assertRaises(HttpError):
            csrf_check(Req)
        Req.headers = {"X-Requested-With": "ExamShield", "Host": "h:1", "Origin": "http://h:1"}
        csrf_check(Req)

    def test_password_hash(self):
        from server.routes.auth import hash_password, verify_password
        h, s = hash_password("pw1")
        self.assertTrue(verify_password("pw1", h, s))
        self.assertFalse(verify_password("pw2", h, s))


class TestNotesValidation(unittest.TestCase):
    def test_extension_and_magic(self):
        self.assertEqual(notes.validate("a.pdf", b"%PDF-1.4 x"), "pdf")
        for name, data in (("a.exe", b"MZ"), ("a.pdf", b"not pdf"), ("a.txt", b"\xff\xfe\x00bad"), ("a.png", b"xx")):
            with self.assertRaises(HttpError):
                notes.validate(name, data)

    def test_size_limit_and_filename(self):
        with self.assertRaises(HttpError) as c:
            notes.validate("a.txt", b"x" * (cfg.MAX_UPLOAD_MB * 1024 * 1024 + 1))
        self.assertEqual(c.exception.status, 413)
        self.assertEqual(notes.clean_filename("../../etc/pass wd.txt"), "pass wd.txt")


if __name__ == "__main__":
    unittest.main()
