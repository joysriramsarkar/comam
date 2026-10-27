"""Deterministic Bengali number and unit parsing.

Shopkeepers say numbers like পঁচাত্তর (75), সাড়ে তিন (3.5), দেড় কেজি (1.5 kg),
৫০০ গ্রাম (500 g). None of that is left to the LLM: this module parses it in
code, with tests (backend/tests/test_bn.py).
"""
import re

BN_DIGITS = "০১২৩৪৫৬৭৮৯"
_TO_ASCII = str.maketrans(BN_DIGITS, "0123456789")
_TO_BN = str.maketrans("0123456789", BN_DIGITS)


def bn_to_ascii(text: str) -> str:
    return text.translate(_TO_ASCII)


def to_bn(text) -> str:
    return str(text).translate(_TO_BN)


def fmt_num(value) -> str:
    """Format a number for Bengali display: 285 -> '২৮৫', 2.5 -> '২.৫'."""
    if value is None:
        return ""
    f = float(value)
    s = str(int(f)) if f == int(f) else str(f)
    return to_bn(s)


def fmt_taka(value) -> str:
    return f"{fmt_num(value)} টাকা"


WORD_NUMBERS = {
    "শূন্য": 0, "এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5, "ছয়": 6,
    "সাত": 7, "আট": 8, "নয়": 9, "দশ": 10, "এগারো": 11, "বারো": 12,
    "তেরো": 13, "চৌদ্দ": 14, "পনেরো": 15, "ষোলো": 16, "সতেরো": 17,
    "আঠারো": 18, "উনিশ": 19, "বিশ": 20, "কুড়ি": 20, "একুশ": 21,
    "বাইশ": 22, "তেইশ": 23, "চব্বিশ": 24, "পঁচিশ": 25, "ছাব্বিশ": 26,
    "সাতাশ": 27, "আটাশ": 28, "ঊনত্রিশ": 29, "ত্রিশ": 30, "একত্রিশ": 31,
    "বত্রিশ": 32, "তেত্রিশ": 33, "চৌত্রিশ": 34, "পঁয়ত্রিশ": 35,
    "ছত্রিশ": 36, "সাঁইত্রিশ": 37, "আটত্রিশ": 38, "ঊনচল্লিশ": 39,
    "চল্লিশ": 40, "একচল্লিশ": 41, "বিয়াল্লিশ": 42, "তেতাল্লিশ": 43,
    "চুয়াল্লিশ": 44, "পঁয়তাল্লিশ": 45, "ছেচল্লিশ": 46, "সাতচল্লিশ": 47,
    "আটচল্লিশ": 48, "ঊনপঞ্চাশ": 49, "পঞ্চাশ": 50, "একান্ন": 51,
    "বাহান্ন": 52, "তিপ্পান্ন": 53, "চুয়ান্ন": 54, "পঞ্চান্ন": 55,
    "ছাপ্পান্ন": 56, "সাতান্ন": 57, "আটান্ন": 58, "ঊনষাট": 59,
    "ষাট": 60, "একষট্টি": 61, "বাষট্টি": 62, "তেষট্টি": 63,
    "চৌষট্টি": 64, "পঁয়ষট্টি": 65, "ছেষট্টি": 66, "সাতষট্টি": 67,
    "আটষট্টি": 68, "ঊনসত্তর": 69, "সত্তর": 70, "একাত্তর": 71,
    "বাহাত্তর": 72, "তিয়াত্তর": 73, "চুয়াত্তর": 74, "পঁচাত্তর": 75,
    "ছিয়াত্তর": 76, "সাতাত্তর": 77, "আটাত্তর": 78, "ঊনআশি": 79,
    "আশি": 80, "একাশি": 81, "বিরাশি": 82, "তিরাশি": 83, "চুরাশি": 84,
    "পঁচাশি": 85, "ছিয়াশি": 86, "সাতাশি": 87, "আটাশি": 88,
    "ঊননব্বই": 89, "নব্বই": 90, "একানব্বই": 91, "বিরানব্বই": 92,
    "তিরানব্বই": 93, "চুরানব্বই": 94, "পঁচানব্বই": 95, "ছিয়ানব্বই": 96,
    "সাতানব্বই": 97, "আটানব্বই": 98, "নিরানব্বই": 99,
}

# stand-alone fractional words
FRACTION_WORDS = {"আধা": 0.5, "অর্ধেক": 0.5, "দেড়": 1.5, "আড়াই": 2.5, "হাফ": 0.5}

UNITS = {
    "কেজি": ("kg", 1.0), "কিলো": ("kg", 1.0), "কিলোগ্রাম": ("kg", 1.0),
    "গ্রাম": ("kg", 0.001), "লিটার": ("litre", 1.0), "লি": ("litre", 1.0),
    "মিলি": ("litre", 0.001), "টা": ("piece", 1.0), "টে": ("piece", 1.0),
    "টি": ("piece", 1.0), "খানা": ("piece", 1.0), "পিস": ("piece", 1.0),
    "ডজন": ("piece", 12.0),
}


def _base_number(tokens, i):
    """Parse a plain number (digits or 0-99 word, optional শত/হাজার) at tokens[i].
    Returns (value, next_index) or (None, i)."""
    if i >= len(tokens):
        return None, i
    tok = tokens[i]
    ascii_tok = bn_to_ascii(tok)
    value = None
    if re.fullmatch(r"\d+(\.\d+)?", ascii_tok):
        value = float(ascii_tok)
        i += 1
    elif tok.endswith("শ") and tok[:-1] in WORD_NUMBERS and WORD_NUMBERS[tok[:-1]] < 10:
        # colloquial hundreds: দুইশ (200), তিনশ (300), পাঁচশ (500)
        value = float(WORD_NUMBERS[tok[:-1]] * 100)
        i += 1
    elif tok in WORD_NUMBERS:
        value = float(WORD_NUMBERS[tok])
        i += 1
    else:
        return None, i
    # multipliers: একশ/দুই শত/তিন হাজার
    if i < len(tokens) and tokens[i] in ("শত", "শো"):
        value *= 100
        i += 1
        rest, j = _base_number(tokens, i)
        if rest is not None and rest < 100:
            value += rest
            i = j
    elif i < len(tokens) and tokens[i] == "হাজার":
        value *= 1000
        i += 1
        rest, j = _base_number(tokens, i)
        if rest is not None and rest < 1000:
            value += rest
            i = j
    return value, i


def parse_number_at(tokens, i):
    """Parse any Bengali number phrase starting at tokens[i]:
    সাড়ে তিন (3.5), পৌনে চার (3.75), সোয়া দুই (2.25), দেড় (1.5),
    আধা (0.5), plain digits/words. Returns (value, next_index) or (None, i)."""
    if i >= len(tokens):
        return None, i
    tok = tokens[i]
    if tok == "সাড়ে":
        base, j = _base_number(tokens, i + 1)
        if base is not None:
            return base + 0.5, j
        return None, i
    if tok == "পৌনে":
        base, j = _base_number(tokens, i + 1)
        if base is not None:
            return base - 0.25, j
        return None, i
    if tok == "সোয়া":
        base, j = _base_number(tokens, i + 1)
        if base is not None:
            return base + 0.25, j
        return None, i
    if tok in FRACTION_WORDS:
        return FRACTION_WORDS[tok], i + 1
    if tok in ("একশ", "একশো", "শত"):
        rest, j = _base_number(tokens, i + 1)
        if rest is not None and rest < 100:
            return 100 + rest, j
        return 100.0, i + 1
    return _base_number(tokens, i)


def find_number_phrases(text: str):
    """Find all number phrases in text. Returns list of (value, start, end)
    with char offsets into the original text."""
    tokens = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[^\s,।;]+", text)]
    words = [t[0] for t in tokens]
    found = []
    i = 0
    while i < len(words):
        value, j = parse_number_at(words, i)
        if value is not None and j > i:
            found.append((value, tokens[i][1], tokens[j - 1][2]))
            i = j
        else:
            i += 1
    return found
