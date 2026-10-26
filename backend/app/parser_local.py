"""Rule-based Bengali command parser (offline fallback + eval baseline).

Produces the SAME draft shape as the Muse Spark tool-calling path, so the
confirm flow does not care which engine understood the sentence.
Ambiguity is reported, never guessed.
"""
import re

from .bn import UNITS, fmt_taka, find_number_phrases, parse_number_at
from .catalog import find_customer, normalize, search_products

PIECE_WORDS = ("টে", "টা", "টি", "খানা", "পিস")


def _split_items(text):
    parts = re.split(r"[,،]| এবং | আর | ও ", text)
    return [p.strip() for p in parts if p.strip()]


def _parse_item_segment(conn, segment):
    """One segment like 'দুই কেজি চিনি' or '৫০০ গ্রাম মসুর ডাল' or 'তিনটে সাবান'.
    Returns (item_dict_or_None, ambiguity_or_None, leftover_text)."""
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
        cleaned = re.sub(r"(দাও|দিন|দেন|নাও|করো|চাই|লিখে রাখো|রাখো)", "", rest).strip()
        if cleaned != rest:
            matches, ambiguous = search_products(conn, cleaned)
            rest = cleaned
    if not matches:
        return None, None, rest
    product, _score = matches[0]
    if qty is None:
        qty = 1.0
    qty_units = qty * unit_factor
    item = {
        "product_id": product["id"],
        "name_bn": product["name_bn"],
        "qty": qty_units,
        "unit": product["unit"],
        "unit_price": product["price"],
        "line_total": round(product["price"] * qty_units, 2),
    }
    amb = None
    if ambiguous:
        amb = {
            "text": rest,
            "options": [
                {"product_id": p["id"], "name_bn": p["name_bn"], "price": p["price"]}
                for p, _ in matches[:3]
            ],
        }
    return item, amb, rest


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

    # due payment: 'রহিমদা ২০০ টাকা জমা দিলেন'
    if "জমা" in t and customer is not None:
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
    draft["payment_type"] = "due" if ("বাকি" in t or "নামে" in t) else "cash"
    items = []
    for seg in _split_items(text):
        # remove trailing customer/payment phrases from the last segment
        seg_clean = re.sub(r"(র|এর)?\s*নামে.*$", "", seg).strip()
        item, amb, _ = _parse_item_segment(conn, seg_clean or seg)
        if amb:
            draft["ambiguities"].append(amb)
        if item:
            items.append(item)
    draft["items"] = items
    draft["total"] = round(sum(i["line_total"] for i in items), 2)
    if not items:
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
        who = ""
        if draft.get("customer"):
            who = f" ({draft['customer']['name']}-এর নামে, {'বাকি' if draft.get('payment_type') == 'due' else 'নগদ'})"
        verb = "ক্রয়" if kind == "purchase" else "বিক্রি"
        return f"{verb}: " + "; ".join(parts) + f" । মোট {fmt_taka(draft.get('total', 0))}{who}"
    if kind == "due_payment":
        return f"জমা: {draft['customer']['name']} — {fmt_taka(draft.get('amount', 0))}"
    if kind == "undo":
        return "শেষ বিক্রিটা বাতিল করা হবে"
    return kind or ""
