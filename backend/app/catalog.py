"""Catalog matching: Bengali names, spelling variants and Banglish.

'বিস্কুট' matches two products (বড় প্যাকেট ২০ টাকা, ছোট প্যাকেট ১০ টাকা).
That is not an error: the matcher reports ambiguity and the caller asks a
clarification question instead of guessing.
"""
import difflib
import re
import unicodedata

from .db import get_conn, init_db


def normalize(text: str) -> str:
    # NFC folds decomposed sequences (য + ়) into the precomposed য়/ড় —
    # ASR and keyboards produce both spellings of the same name.
    text = unicodedata.normalize("NFC", text.strip().lower())
    text = re.sub(r"[\s\-_]+", " ", text)
    text = re.sub(r"[।,;:!?\"'()]", "", text)
    return text.strip()


def _names(product):
    names = [product["name_bn"], product["name_en"]]
    names += [a.strip() for a in product["aliases"].split(",") if a.strip()]
    return [normalize(n) for n in names]


def search_products(conn, query, limit=5):
    """Return (matches, ambiguous). matches: list of (product_row, score)."""
    q = normalize(query)
    products = conn.execute("SELECT * FROM products").fetchall()
    scored = []
    for p in products:
        best = 0.0
        for name in _names(p):
            if q == name:
                best = 1.0
            elif q and (q in name or name in q):
                best = max(best, 0.9)
            else:
                best = max(best, difflib.SequenceMatcher(None, q, name).ratio())
        if best >= 0.55:
            scored.append((p, best))
    scored.sort(key=lambda x: -x[1])
    scored = scored[:limit]
    ambiguous = (
        len(scored) >= 2
        and scored[1][1] >= 0.55
        and (scored[0][1] - scored[1][1]) < 0.08
    )
    return scored, ambiguous


def _name_variants(word):
    """A spoken name token with its case/honorific endings stripped:
    সুনিতার -> সুনিতা, রহিমদা -> রহিম, করিমকে -> করিম.
    Protects root names ending in 'র' like সরকার or সুধীর."""
    out = {word}
    # Honorifics
    for suf in ("বাবুর", "বাবু", "কাকার", "কাকা", "দার", "দা", "ভাই", "দিদি", "কে"):
        if word.endswith(suf) and len(word) > len(suf) + 1:
            out.add(word[:-len(suf)])
    # Genitive endings
    for suf in ("-এর", "ের"):
        if word.endswith(suf) and len(word) > len(suf) + 1:
            out.add(word[:-len(suf)])
    # Only strip terminal 'র' if preceded by 'া' (e.g. সুনিতার -> সুনিতা, but protect সরকার)
    if word.endswith("ার") and len(word) > 3 and not word.endswith("সরকার"):
        out.add(word[:-1])
    return out


_BN_TO_LATIN = {
    'অ': 'o', 'আ': 'a', 'ই': 'i', 'ঈ': 'i', 'উ': 'u', 'ঊ': 'u', 'ঋ': 'ri',
    'এ': 'e', 'ঐ': 'oi', 'ও': 'o', 'ঔ': 'ou',
    'ক': 'k', 'খ': 'kh', 'গ': 'g', 'ঘ': 'gh', 'ঙ': 'ng',
    'চ': 'ch', 'ছ': 'chh', 'জ': 'j', 'ঝ': 'jh', 'ঞ': 'n',
    'ট': 't', 'ঠ': 'th', 'ড': 'd', 'ঢ': 'dh', 'ণ': 'n',
    'ত': 't', 'থ': 'th', 'দ': 'd', 'ধ': 'dh', 'ন': 'n',
    'প': 'p', 'ফ': 'f', 'ব': 'b', 'ভ': 'bh', 'ম': 'm',
    'য': 'j', 'র': 'r', 'ল': 'l', 'শ': 'sh', 'ষ': 'sh', 'স': 's', 'হ': 'h',
    'ড়': 'r', 'ঢ়': 'rh', 'য়': 'y', 'ৎ': 't', 'ং': 'ng', 'ঃ': '', 'ঁ': '',
    'া': 'a', 'ি': 'i', 'ী': 'i', 'ু': 'u', 'ূ': 'u', 'ৃ': 'ri',
    'ে': 'e', 'ৈ': 'oi', 'ো': 'o', 'ৌ': 'ou', '্': '',
}


def transliterate_bn_to_latin(text: str) -> str:
    if not text:
        return ""
    norm = normalize(text).replace("ক্ষ", "kh")
    out = "".join(_BN_TO_LATIN.get(ch, ch) for ch in norm)
    return re.sub(r"\s+", " ", out).strip()


def find_customer(conn, text):
    """Find a customer mentioned by name/alias inside free text."""
    t = normalize(text)
    customers = conn.execute("SELECT * FROM customers").fetchall()
    for c in customers:
        names = [c["name"]] + [a.strip() for a in c["aliases"].split(",") if a.strip()]
        for n in names:
            nn = normalize(n)
            if nn and nn in t:
                return c
    # Fuzzy fallback: dictation/ASR near-miss spellings, e.g. 'সুনিতা' for
    # 'সুমিতা' or Latin/English database names.
    words = t.split()
    windows = list(words) + [" ".join(words[i:i + 2]) for i in range(len(words) - 1)]
    scored = []
    for c in customers:
        names = [c["name"]] + [a.strip() for a in c["aliases"].split(",") if a.strip()]
        best = 0.0
        for n in names:
            nn = normalize(n)
            nn_lat = transliterate_bn_to_latin(nn) if re.search(r"[\u0980-\u09FF]", nn) else nn.lower()
            for w in windows:
                for cand in _name_variants(w):
                    # Bengali direct match
                    best = max(best, difflib.SequenceMatcher(None, cand, nn).ratio())
                    # Latin transliteration match
                    cand_lat = transliterate_bn_to_latin(cand)
                    if cand_lat and nn_lat:
                        best = max(best, difflib.SequenceMatcher(None, cand_lat, nn_lat).ratio())
        scored.append((best, c))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] >= 0.78 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
        return scored[0][1]
    return None


# Words that are never part of a person's name in a shop command.
_NAME_STOP = {
    "বিল", "করো", "করুন", "দাও", "দিন", "দেন", "নাও", "চাই", "বাকি", "বাকিতে",
    "নগদ", "নগদে", "টাকা", "টাকার", "কেজি", "গ্রাম", "লিটার", "ডজন", "আর",
    "এবং", "ও", "জমা", "দিলেন", "দিল", "কিনলাম", "কিনেছি", "এনেছি", "আনলাম",
    "বিক্রি", "সেল", "ইউপিআই", "ইউপিআইতে",
}


def extract_new_customer_name(conn, text):
    """Heuristic: a name-like phrase right before 'কে' or 'র নামে' that is
    NOT a known customer, e.g. 'জয় সরকারকে তিনটা ডিম ...' -> 'জয় সরকার'.
    Returns the cleaned name, or None. Conservative: 1-3 Bengali words,
    no command/stop words, not a product name."""
    t = normalize(text)
    for pat in (r"([\u0980-\u09FF]+(?: [\u0980-\u09FF]+){0,2})কে(?: |$)",
                r"([\u0980-\u09FF]+(?: [\u0980-\u09FF]+){0,2})র? নামে"):
        m = re.search(pat, t)
        if not m:
            continue
        words = m.group(1).split()
        # drop any leading run of stop words the regex may have swallowed
        # ('নতুন ক্রেতা জয় সরকারের নামে' must not keep the prefix either)
        while words and (words[0] in _NAME_STOP or words[0] in ("নতুন", "ক্রেতা")):
            words.pop(0)
        if not words or any(w in _NAME_STOP for w in words):
            continue
        # strip a genitive ending: সরকারের -> সরকার
        last = words[-1]
        if last.endswith("ের") and len(last) > 3:
            words[-1] = last = last[:-2]
        # strip honorifics: করিমদা -> করিম, জয়দার -> জয়
        if last.endswith("দার") and len(last) > 3:
            words[-1] = last[:-3]
        elif last.endswith("দা") and len(last) > 3:
            words[-1] = last[:-2]
        name = " ".join(words).strip()
        if not name:
            continue
        matches, _ = search_products(conn, name)
        if matches and matches[0][1] >= 0.9:
            continue  # that phrase is a product, not a person
        return name
    return None


def extract_payer_name(conn, text):
    """Name at the START of a due-payment sentence with no কে/নামে marker:
    'জয় সরকার ২০০ টাকা জমা দিলেন' -> 'জয় সরকার'. Leading Bengali words
    up to the first number token; conservative like extract_new_customer_name."""
    from .bn import WORD_NUMBERS, bn_to_ascii

    words = normalize(text).split()
    name_words = []
    for w in words:
        if len(name_words) >= 3:
            break
        if (re.fullmatch(r"\d+(\.\d+)?", bn_to_ascii(w)) or w in WORD_NUMBERS
                or w in _NAME_STOP or w in ("সাড়ে", "পৌনে", "সোয়া", "দেড়", "আড়াই")):
            break
        if not re.fullmatch(r"[\u0980-\u09FF]+", w):
            break
        name_words.append(w)
    if not name_words or len(name_words) == len(words):
        return None  # stopped immediately, or never reached a number
    last = name_words[-1]
    if last.endswith("ের") and len(last) > 3:
        name_words[-1] = last = last[:-2]
    if last.endswith("দার") and len(last) > 3:
        name_words[-1] = last[:-3]
    elif last.endswith("দা") and len(last) > 3:
        name_words[-1] = last[:-2]
    name = " ".join(name_words).strip()
    if not name or any(w in _NAME_STOP for w in name_words):
        return None
    matches, _ = search_products(conn, name)
    if matches and matches[0][1] >= 0.9:
        return None
    return name


def quantity_warnings(items):
    """Advisory flags for unusually large quantities — never blocking.

    Spoken batch 2 showed ASR hearing পৌনে (1.75) as পনেরো (15): the
    parser cannot safely remap a real number, so the mitigation is to
    make a large quantity impossible to miss on the confirm read-back.
    Thresholds sit above ordinary small-shop amounts (a 10 kg purchase
    stays silent; 15 kg of আলু does not)."""
    out = []
    for i in items:
        qty, unit = float(i.get("qty") or 0), i.get("unit")
        if (unit in ("kg", "litre") and qty > 10) or (unit == "piece" and qty > 100):
            out.append(
                f"{i['name_bn']} {qty:g} {unit} — পরিমাণটা বড়, ঠিক আছে কিনা দেখে নিন"
            )
    return out


if __name__ == "__main__":
    conn = init_db(get_conn(":memory:"))
    m, amb = search_products(conn, "বিস্কুট")
    print([(p["name_bn"], round(s, 2)) for p, s in m], "ambiguous:", amb)
