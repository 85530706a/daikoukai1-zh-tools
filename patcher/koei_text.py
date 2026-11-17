"""Text encoding of the Taiwanese DOS release of Daikoukai Jidai (MAIN.EXE).

Chinese characters are stored as 2-byte codes, lead byte 0x92 and up. The code
is a Big5 level-1 index written in rows of 188, where the trail byte skips the
ASCII punctuation the text engine uses for control sequences. A few small
offsets (SEGS) were measured against the game's own strings.

Decoding is about 97 % accurate on the game's text; the remaining misses are
rare characters. Replacements in apply_patch.py only reuse codes that already
appear in the game's text, so they always display correctly.
"""

VALID_TRAIL = (list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)) +
               list(range(0x61, 0x7B)) + list(range(0x80, 0xFE)))
RANK = {t: i for i, t in enumerate(VALID_TRAIL)}          # 188 per row
SEGS = [(0, -94), (1515, -93), (1947, -94), (3834, -95), (4208, -94), (4600, -95)]


def _kk(code):
    lead, trail = code >> 8, code & 0xFF
    if trail not in RANK or lead < 0x92:
        return None
    return (lead - 0x92) * 188 + RANK[trail]


def _delta(k):
    d = SEGS[0][1]
    for start, value in SEGS:
        if k >= start:
            d = value
    return d


def _big5(k):
    if k < 0:
        return None
    hi, c = 0xA4 + k // 157, k % 157
    lo = 0x40 + c if c < 63 else 0xA1 + c - 63
    if hi > 0xFE:
        return None
    try:
        return bytes([hi, lo]).decode("big5")
    except UnicodeDecodeError:
        return None


def code_to_char(code):
    k = _kk(code)
    return None if k is None else _big5(k + _delta(k))


def tokens(buf):
    """Split a string into ('c', code, char) and ('b', byte, None) tokens."""
    out, i = [], 0
    while i < len(buf):
        if 0x92 <= buf[i] < 0xFE and i + 1 < len(buf):
            code = int.from_bytes(buf[i:i + 2], "big")
            out.append(("c", code, code_to_char(code)))
            i += 2
        else:
            out.append(("b", buf[i], None))
            i += 1
    return out


def encode(toks):
    return b"".join(t[1].to_bytes(2, "big") if t[0] == "c" else bytes([t[1]]) for t in toks)


def find_strings(image, known_chars):
    """Yield (offset, bytes) of NUL-terminated runs that decode mostly to
    characters in `known_chars` (the game's own character set)."""
    import re
    for m in re.finditer(rb"[^\x00]{2,}", image):
        good = bad = 0
        for kind, _, ch in tokens(m.group()):
            if kind == "c":
                if ch in known_chars:
                    good += 1
                else:
                    bad += 1
        if good >= 2 and good >= 3 * bad:
            yield m.start(), m.group()
