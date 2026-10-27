"""Catalog matching: Bengali names, spelling variants and Banglish.

'বিস্কুট' matches two products (বড় প্যাকেট ২০ টাকা, ছোট প্যাকেট ১০ টাকা).
That is not an error: the matcher reports ambiguity and the caller asks a
clarification question instead of guessing.
"""
import difflib
import re

from .db import get_conn, init_db


def normalize(text: str) -> str:
    text = text.strip().lower()
    text = text.replace("য়", "য়").replace("ড়", "ড়")
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
    সুনিতার -> সুনিতা, রহিমদা -> রহিম, করিমকে -> করিম."""
    out = {word}
    for suf in ("দার", "দা", "কে", "র"):
        if word.endswith(suf) and len(word) > len(suf) + 1:
            out.add(word[: -len(suf)])
    return out


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
    # 'সুমিতা'. Accept only a clear winner (>= 0.8 and ahead of the
    # runner-up customer), never a coin flip between two customers.
    words = t.split()
    windows = list(words) + [" ".join(words[i:i + 2]) for i in range(len(words) - 1)]
    scored = []
    for c in customers:
        names = [c["name"]] + [a.strip() for a in c["aliases"].split(",") if a.strip()]
        best = 0.0
        for n in names:
            nn = normalize(n)
            for w in windows:
                for cand in _name_variants(w):
                    best = max(best, difflib.SequenceMatcher(None, cand, nn).ratio())
        scored.append((best, c))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
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
        while words and words[0] in _NAME_STOP:
            words.pop(0)
        if not words or any(w in _NAME_STOP for w in words):
            continue
        # strip honorifics: করিমদা -> করিম, জয়দার -> জয়
        last = words[-1]
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


if __name__ == "__main__":
    conn = init_db(get_conn(":memory:"))
    m, amb = search_products(conn, "বিস্কুট")
    print([(p["name_bn"], round(s, 2)) for p, s in m], "ambiguous:", amb)
