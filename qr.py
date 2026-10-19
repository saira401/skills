"""Hand-written QR encoder (byte mode, ECC level L, versions 1-10) + PNG writer + terminal renderer."""
import struct
import zlib

# version -> (ec codewords per block, [(block count, data codewords per block), ...])
EC_L = {1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]), 4: (20, [(1, 80)]),
        5: (26, [(1, 108)]), 6: (18, [(2, 68)]), 7: (20, [(2, 78)]), 8: (24, [(2, 97)]),
        9: (30, [(2, 116)]), 10: (18, [(2, 68), (2, 69)])}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
         7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
FORMAT_L = 1  # ECC level L format bits


def _mul(x, y):  # GF(256), polynomial 0x11D
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _mul(root, 2)
    return result


def rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, c in enumerate(divisor):
            result[i] ^= _mul(c, factor)
    return result


def _codewords(data, ver):
    ecl, groups = EC_L[ver]
    ncw = sum(n * d for n, d in groups)
    cc = 8 if ver < 10 else 16
    bits = "0100" + format(len(data), f"0{cc}b") + "".join(format(b, "08b") for b in data)
    bits += "0" * min(4, ncw * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    i = 0
    while len(cw) < ncw:
        cw.append((0xEC, 0x11)[i % 2])
        i += 1
    blocks, pos = [], 0
    for n, d in groups:
        for _ in range(n):
            blocks.append(cw[pos:pos + d])
            pos += d
    div = rs_divisor(ecl)
    ecs = [rs_remainder(b, div) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ecl):
        for e in ecs:
            out.append(e[i])
    return out


MASKS = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
         lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
         lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
         lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]


def format_bits(mask):
    data = (FORMAT_L << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _penalty(m):
    n = len(m)
    score = 0
    lines = ["".join("1" if c else "0" for c in row) for row in m]
    lines += ["".join("1" if m[y][x] else "0" for y in range(n)) for x in range(n)]
    for s in lines:
        run = 1
        for i in range(1, n + 1):
            if i < n and s[i] == s[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run = 1
        for pat in ("10111010000", "00001011101"):
            i = s.find(pat)
            while i != -1:
                score += 40
                i = s.find(pat, i + 1)
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    total = n * n
    score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
    return score


def make(data):
    """Return the QR matrix (list of rows of bool, True = dark) for bytes/str `data`."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    for ver in range(1, 11):
        ncw = sum(n * d for n, d in EC_L[ver][1])
        if 4 + (8 if ver < 10 else 16) + len(data) * 8 <= ncw * 8:
            break
    else:
        raise ValueError("Data too long for QR versions 1-10 (max 271 bytes)")
    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        if 0 <= x < size and 0 <= y < size:
            mod[y][x] = bool(dark)
            fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                setf(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    pos = ALIGN[ver]
    for i, py in enumerate(pos):
        for j, px in enumerate(pos):
            if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(px + dx, py + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        b = format_bits(mask)
        bit = lambda i: (b >> i) & 1
        for i in range(6):
            setf(8, i, bit(i))
        setf(8, 7, bit(6)); setf(8, 8, bit(7)); setf(7, 8, bit(8))
        for i in range(9, 15):
            setf(14 - i, 8, bit(i))
        for i in range(8):
            setf(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, bit(i))
        setf(8, size - 8, True)

    draw_format(0)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (ver << 12) | rem
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, (bits >> i) & 1)
            setf(b, a, (bits >> i) & 1)
    cws = _codewords(data, ver)
    i = 0
    for right in range(size - 1, 0, -2):
        if right <= 6:
            right -= 1
        up = ((right + 1) & 2) == 0
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if up else vert
                if not fn[y][x] and i < len(cws) * 8:
                    mod[y][x] = bool((cws[i >> 3] >> (7 - (i & 7))) & 1)
                    i += 1

    def apply(mask):
        f = MASKS[mask]
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and f(x, y):
                    mod[y][x] = not mod[y][x]

    best, best_pen = 0, None
    for mask in range(8):
        draw_format(mask)
        apply(mask)
        pen = _penalty(mod)
        apply(mask)
        if best_pen is None or pen < best_pen:
            best, best_pen = mask, pen
    draw_format(best)
    apply(best)
    return mod


def png(matrix, scale=8, border=4):
    """1-bit grayscale PNG (dark = black)."""
    n = len(matrix)
    dim = (n + 2 * border) * scale
    rows = bytearray()
    for py in range(dim):
        my = py // scale - border
        bits = []
        for px in range(dim):
            mx = px // scale - border
            dark = 0 <= mx < n and 0 <= my < n and matrix[my][mx]
            bits.append("0" if dark else "1")
        s = "".join(bits)
        s += "1" * (-len(s) % 8)
        rows += b"\x00" + int(s, 2).to_bytes(len(s) // 8, "big")

    def chunk(tag, body):
        c = struct.pack(">I", len(body)) + tag + body
        return c + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", dim, dim, 1, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def terminal(matrix, border=2):
    """Half-block renderer: black modules on a white background, readable on dark terminals."""
    n = len(matrix)
    get = lambda x, y: 0 <= x < n and 0 <= y < n and matrix[y][x]
    lines = []
    for y in range(-border, n + border, 2):
        row = ""
        for x in range(-border, n + border):
            t, b = get(x, y), get(x, y + 1)
            row += "█" if t and b else "▀" if t else "▄" if b else " "
        lines.append("\x1b[30;47m" + row + "\x1b[0m")
    return "\n".join(lines)
