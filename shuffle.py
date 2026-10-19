"""Seeded Fisher-Yates: the same attempt always sees the same order (refresh-safe)."""
import hashlib

M64 = 0xFFFFFFFFFFFFFFFF


def _rng(seed):
    state = int.from_bytes(hashlib.sha256(str(seed).encode()).digest()[:8], "big") or 1

    def nxt(n):  # xorshift64*
        nonlocal state
        state ^= state >> 12
        state ^= (state << 25) & M64
        state ^= state >> 27
        return ((state * 0x2545F4914F6CDD1D) & M64) % n
    return nxt


def seeded_shuffle(items, seed):
    out, nxt = list(items), _rng(seed)
    for i in range(len(out) - 1, 0, -1):
        j = nxt(i + 1)
        out[i], out[j] = out[j], out[i]
    return out
