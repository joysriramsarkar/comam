"""Rule-based Bengali command parser (offline fallback + eval baseline).

Produces the SAME draft shape as the Muse Spark tool-calling path, so the
confirm flow does not care which engine understood the sentence.
Ambiguity is reported, never guessed.
"""
import re

from .bn import UNITS, fmt_taka, find_number_phrases, parse_number_at
from .catalog import extract_new_customer_name, find_customer, normalize, search_products

PIECE_WORDS = ("টে", "টা", "টি", "খানা", "পিস")


def _split_items(text):
    parts = re.split(r"[,،]| এবং | আর | ও ", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_stated_price(segment):
    """'<num> টাকার X' / '<num> টাকা দামের X': the number states a PRICE,
    not a quantity. Returns (stated_price_or_None, segment_without_phrase)."""
    for v, s, e in find_number_phrases(segment):
        after = segment[e:]
        stripped = after.lstrip()
        lead = len(after) - len(stripped)
        for token in ("টাকা দামের", "টাকার"):
            if stripped.startswith(token):
                cut = e + lead + len(token)
                return v, (segment[:s] + " " + segment[cut:]).strip()
    return None, segment


def _parse_item_segment(conn, segment):
    """One segment like 'দুই কেজি চিনি' or '৫০০ গ্রাম মসুর ডাল' or 'তিনটে সাবান'.
    Returns (item_or_None, ambiguity_or_None, leftover_text, info) where
    info carries qty/stated_price for unknown-item reporting."""
    stated_price, segment = _extract_stated_price(segment)
    phrases = find_number_phrases(segment)
    qty = None
    qty_span = None
    if phrases:
        qty, s, e = phrases[0]
        qty_span = (s, e)
    unit_factor = 1.0
    unit_kind = None
    rest = segment
    if qty_span:
        after = segment[qty_span[1]:].strip()
        for uw, (uk, factor) in sorted(UNITS.items(), key=lambda kv: -len(kv[0])):
            if after.startswith(uw):
                unit_kind, unit_factor = uk, factor
                rest = (segment[: qty_span[0]] + " " + after[len(uw):]).strip()
                break
        else:
            rest = (segment[: qty_span[0]] + " " + segment[qty_span[1]:]).strip()
    if qty is None:
        # piece suffix stuck to number word: 'তিনটে' handled by find_number_phrases?
        # NOTE: no \b here — Bengali vowel signs are combining marks, so
        # Python's \b fails after them. Use an explicit lookahead instead.
        m = re.search(r"(\S+?)(টে|টা|টি|খানা)(?=$|[\s,।])", segment)
        if m:
            v, _ = parse_number_at([m.group(1)], 0)
            if v is not None:
                qty = v
                unit_kind = "piece"
                rest = (segment[: m.start()] + " " + segment[m.end():]).strip()
    matches, ambiguous = search_products(conn, rest)
    if not matches:
        # try dropping filler verbs
        cleaned = re.sub(r"(দাও|দিন|দেন|নাও|করো|করুন|চাই|লিখে রাখো|রাখো|বিল)", "", rest).strip()
        if cleaned != rest:
            matches, ambiguous = search_products(conn, cleaned)
            rest = cleaned
    info = {"qty": qty, "stated_price": stated_price}
    if not matches:
        return None, None, rest, info
    if qty is None:
        qty = 1.0
    # A stated price picks the variant whose price it is:
    # 'দশ টাকা দামের বিস্কুট' = the 10-taka (small) pack, qty 1 — NOT 10 packs.
    if stated_price is not None:
        price_hits = [p for p, _ in matches if abs(p["price"] - stated_price) < 0.01]
        if len(price_hits) == 1:
            product = price_hits[0]
            qty_units = qty * unit_factor
            item = {
                "product_id": product["id"],
                "name_bn": product["name_bn"],
                "qty": qty_units,
                "unit": product["unit"],
                "unit_price": product["price"],
                "line_total": round(product["price"] * qty_units, 2),
            }
            return item, None, rest, info
    product, _score = matches[0]
    if stated_price is not None and len(matches) == 1 and product["unit"] in ("kg", "litre"):
        # '৭৫ টাকার চিনি' = 75 taka WORTH of a weight-priced good.
        qty_units = round(stated_price / product["price"], 3)
        line_total = round(stated_price, 2)
    else:
        qty_units = qty * unit_factor
        line_total = round(product["price"] * qty_units, 2)
    item = {
        "product_id": product["id"],
        "name_bn": product["name_bn"],
        "qty": qty_units,
        "unit": product["unit"],
        "unit_price": product["price"],
        "line_total": line_total,
    }
    amb = None
    if ambiguous:
        options = [
            {"product_id": p["id"], "name_bn": p["name_bn"], "price": p["price"]}
            for p, _ in matches[:3]
        ]
        amb = {"text": rest, "options": options, "product_id": product["id"]}
        # placeholder choice, flagged: confirm stays blocked until the
        # shopkeeper picks one of the options (resolve endpoint).
        item["ambiguous"] = True
        item["options"] = options
    return item, amb, rest, info


_FILLER = re.compile(
    r"(বিল|করো|করুন|দাও|দিন|দেন|নাও|চাই|রাখো|ইউপিআইতে|ইউপিআই|টাকার|দামের|টা|টে|টি)"
)


def _unknown_text(rest):
    """Leftover words that look like a product the catalog does not have."""
    cleaned = _FILLER.sub(" ", rest)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) >= 2 else ""


def parse_command(conn, text):
    """Return a draft dict:
    {kind, items?, customer?, payment_type?, amount?, answer?, ambiguities, raw}
    kind: sale | due_payment | due_query | report | undo | unknown
    """
    t = normalize(text)
    draft = {"raw": text, "ambiguities": []}

    if "বাতিল" in t or "undo" in t:
        draft["kind"] = "undo"
        return draft

    customer = find_customer(conn, text)
    if customer is not None:
        draft["customer"] = {"id": customer["id"], "name": customer["name"]}
    else:
        new_name = extract_new_customer_name(conn, text)
        if new_name:
            # not in the customer book yet: carried on the draft, and the
            # ledger creates the customer when the draft is confirmed.
            draft["customer_name"] = new_name

    # due payment: 'রহিমদা ২০০ টাকা জমা দিলেন'
    if "জমা" in t and (customer is not None or draft.get("customer_name")):
        phrases = find_number_phrases(text)
        if phrases:
            draft["kind"] = "due_payment"
            draft["amount"] = phrases[0][0]
            return draft

    # due query: 'সুমিতার কত বাকি'
    if "কত বাকি" in t or "বাকি কত" in t:
        draft["kind"] = "due_query"
        return draft

    # reports
    if any(w in t for w in ("বিক্রি কেমন", "কত বিক্রি", "বিক্রি কত")):
        draft["kind"] = "report"
        draft["report"] = "week_compare" if "সপ্তাহ" in t else "sales_summary"
        return draft
    if "শেষ হয়ে" in t or "কম আছে" in t or "স্টক" in t:
        draft["kind"] = "report"
        draft["report"] = "low_stock"
        return draft
    if "বেশি বিক্রি" in t or "টপ" in t:
        draft["kind"] = "report"
        draft["report"] = "top_products"
        return draft

    # otherwise: a sale (or purchase). Purchases say কিনলাম/কিনেছি/এনেছি.
    draft["kind"] = "purchase" if any(w in t for w in ("কিনলাম", "কিনেছি", "এনেছি", "আনলাম")) else "sale"
    if "বাকি" in t or "নামে" in t:
        draft["payment_type"] = "due"
    elif "ইউপিআই" in t or "upi" in t:
        draft["payment_type"] = "upi"
    else:
        draft["payment_type"] = "cash"
    cust_names = []
    if customer is not None:
        cust_names = [customer["name"]] + [
            a.strip() for a in customer["aliases"].split(",") if a.strip()
        ]
    item_source = text
    if draft.get("customer_name"):
        item_source = item_source.replace(draft["customer_name"] + "কে", " ")
        item_source = item_source.replace(draft["customer_name"], " ")
    items = []
    unknown_items = []
    for seg in _split_items(item_source):
        # strip customer mentions + payment words wherever they appear
        # ('রহিমদার নামে দুই কেজি চিনি' and '...চিনি, রহিমদার নামে' alike)
        s = seg
        for n in sorted(cust_names, key=len, reverse=True):
            s = s.replace(n, " ")
        s = re.sub(r"(নামে|বাকিতে|বাকি|নগদে|নগদ|ইউপিআইতে|ইউপিআই)", " ", s)
        # honorific residue after name stripping: 'রহিমদার' -> 'দার'
        s = re.sub(r"(^|\s)(দার|দা)(\s|$)", " ", s)
        if len(s.strip()) < 2:
            # residue like 'র' from 'রহিমদার' is not an item segment;
            # a 1-letter query would substring-match half the catalog.
            continue
        item, amb, rest, info = _parse_item_segment(conn, s.strip())
        if amb:
            draft["ambiguities"].append(amb)
        if item:
            items.append(item)
        else:
            # never drop a spoken item silently: flag what the catalog
            # does not know, with any price the shopkeeper stated.
            utext = _unknown_text(rest)
            if utext:
                unknown_items.append(
                    {
                        "text": utext,
                        "qty": info.get("qty") or 1.0,
                        "stated_price": info.get("stated_price"),
                    }
                )
    draft["items"] = items
    if unknown_items:
        draft["unknown_items"] = unknown_items
    draft["total"] = round(sum(i["line_total"] for i in items), 2)
    if not items and not unknown_items:
        draft["kind"] = "unknown"
    return draft


def draft_summary_bn(draft):
    """One-line Bengali read-back of a draft, for the confirm screen."""
    kind = draft.get("kind")
    if kind in ("sale", "purchase"):
        parts = [
            f"{i['name_bn']} {i['qty']:g} {i['unit']} — {fmt_taka(i['line_total'])}"
            for i in draft.get("items", [])
        ]
        pay_bn = {"due": "বাকি", "upi": "ইউপিআই", "cash": "নগদ"}.get(
            draft.get("payment_type"), "নগদ"
        )
        who = ""
        cust = draft.get("customer") or (
            {"name": draft["customer_name"]} if draft.get("customer_name") else None
        )
        if cust:
            new_mark = " (নতুন ক্রেতা)" if draft.get("customer_name") else ""
            who = f" ({cust['name']}-এর নামে{new_mark}, {pay_bn})"
        verb = "ক্রয়" if kind == "purchase" else "বিক্রি"
        out = f"{verb}: " + "; ".join(parts) + f" । মোট {fmt_taka(draft.get('total', 0))}{who}"
        if draft.get("unknown_items"):
            names = ", ".join(u["text"] for u in draft["unknown_items"])
            out += f" । ⚠ ক্যাটালগে নেই: {names}"
        return out
    if kind == "due_payment":
        name = (draft.get("customer") or {}).get("name") or draft.get("customer_name", "")
        return f"জমা: {name} — {fmt_taka(draft.get('amount', 0))}"
    if kind == "undo":
        return "শেষ বিক্রিটা বাতিল করা হবে"
    return kind or ""
