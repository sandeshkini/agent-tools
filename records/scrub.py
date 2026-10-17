"""Secret and personal-number scrub for the librarian.

Runs on the machine that owns the keys.env, so values never leave it:
- every value from ~/Documents/secrets/keys.env becomes [secret NAME];
- token patterns (keys, bearer tokens, JWTs, private keys, long hex) become [secret];
- IP addresses become [ip];
- card, account and phone numbers become [number];
- email addresses become [email].
Names, users and doc paths in keys.env (keys ending _NAME, _USER, _DOCS) are not secrets and are left alone.
"""
import os
import re

KEYS_ENV = os.path.expanduser("~/Documents/secrets/keys.env")

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"\b[a-f0-9]{40,}\b"),
    re.compile(r"(?i)\b(password|passwd|passcode|pin|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^\s'\"\]]{6,}"),
]
OTHER_PATTERNS = [
    (re.compile(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "[ip]"),
    (re.compile(r"\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){5,7}\b"), "[ip]"),
    # masked or partial account/card numbers: 8-****-4500, ***-3801, ****1234, x1234
    (re.compile(r"[\d*•xX]{0,4}[-–]?[*•]{2,}[-–]?\d{2,}"), "[number]"),
    # card/account words followed by digits: "VISA 4017", "account ending 1234", "acct #12345"
    (re.compile(r"(?i)\b(visa|mastercard|amex|card|account|acct|chequing|checking|savings|transit|routing)"
                r"(\s+(ending|ends)(\s+in)?|\s*#|\s+no\.?)?\s+\d{3,}\b"), r"\1 [number]"),
    (re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b"), "[number]"),
    (re.compile(r"(?<![\w.])\+?1?[ .(-]{0,2}\d{3}[ .)-]{1,2}\d{3}[ .-]\d{4}\b"), "[number]"),
    (re.compile(r"\b(?<!\.)\d{9,}\b"), "[number]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[email]"),
]


def load_values():
    vals = []
    try:
        for line in open(KEYS_ENV):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.replace("export ", "").strip()
            v = v.strip().strip("'\"")
            if len(v) >= 8 and not k.endswith(("_NAME", "_USER", "_DOCS")):
                vals.append((k, v))
    except FileNotFoundError:
        pass
    vals.sort(key=lambda kv: -len(kv[1]))
    return vals


_VALUES = load_values()
HITS = {"secret": 0, "other": 0}


def scrub(text: str) -> str:
    if not text:
        return text or ""
    for k, v in _VALUES:
        if v in text:
            HITS["secret"] += text.count(v)
            text = text.replace(v, f"[secret {k}]")
    for p in SECRET_PATTERNS:
        text, n = p.subn("[secret]", text)
        HITS["secret"] += n
    for p, rep in OTHER_PATTERNS:
        text, n = p.subn(rep, text)
        HITS["other"] += n
    return text


def leaks(text: str):
    """What a final check should find nothing of (values and token patterns only)."""
    found = [k for k, v in _VALUES if v in text]
    found += [p.pattern[:30] for p in SECRET_PATTERNS[:8] if p.search(text)]
    found += ["ip"] if OTHER_PATTERNS[0][0].search(text) else []
    return found
