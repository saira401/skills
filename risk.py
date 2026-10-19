"""Event weights, strike rules, risk levels, Levenshtein."""
WEIGHTS = {
    "tab_switch": 3, "fullscreen_exit": 3, "second_tab": 3, "session_takeover": 4,
    "copy": 1, "paste": 1, "cut": 1, "contextmenu": 1, "shortcut": 1,
    "fast_answer": 1, "same_ip": 1, "similar_answer": 2, "auto_submit": 0,
}
STRIKE_KINDS = {"tab_switch", "fullscreen_exit", "second_tab", "session_takeover"}
CLIENT_KINDS = {"tab_switch", "fullscreen_exit", "second_tab", "copy", "paste", "cut", "contextmenu", "shortcut"}
FOCUS_KINDS = {"tab_switch", "fullscreen_exit"}


def level_for(score, auto_submitted=False):
    if auto_submitted or score >= 12:
        return "high"
    return "medium" if score >= 5 else "low"


def compute_risk(kinds, auto_submitted=False):
    score = sum(WEIGHTS.get(k, 0) for k in kinds)
    return {"score": score, "level": level_for(score, auto_submitted),
            "strikes": sum(1 for k in kinds if k in STRIKE_KINDS),
            "focus_loss": sum(1 for k in kinds if k in FOCUS_KINDS)}


def levenshtein(a, b):
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
