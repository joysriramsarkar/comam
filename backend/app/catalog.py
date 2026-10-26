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


def find_customer(conn, text):
    """Find a customer mentioned by name/alias inside free text."""
    t = normalize(text)
    for c in conn.execute("SELECT * FROM customers").fetchall():
        names = [c["name"]] + [a.strip() for a in c["aliases"].split(",") if a.strip()]
        for n in names:
            nn = normalize(n)
            if nn and nn in t:
                return c
    return None


if __name__ == "__main__":
    conn = init_db(get_conn(":memory:"))
    m, amb = search_products(conn, "বিস্কুট")
    print([(p["name_bn"], round(s, 2)) for p, s in m], "ambiguous:", amb)
