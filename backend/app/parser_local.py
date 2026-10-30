"""Rule-based Bengali command parser (offline fallback + eval baseline).

Produces the SAME draft shape as the Muse Spark tool-calling path, so the
confirm flow does not care which engine understood the sentence.
Ambiguity is reported, never guessed.
"""
import re

from .bn import (
    UNITS,
    conflicting_amounts,
    fmt_taka,
    find_number_phrases,
    parse_number_at,
)
from .catalog import (
    extract_new_customer_name,
    extract_payer_name,
    find_customer,
    normalize,
    quantity_warnings,
    search_products,
)

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
        for token in ("টাকা দামের", "টাকার", "টাকা"):
            if stripped.startswith(token):
                cut = e + lead + len(token)
                if token == "টাকা" and not segment[cut:].strip():
                    # '…২০০ টাকা' at the END of a segment is a stated
                    # total, not a per-item price — leave it alone.
                    continue
                return v, (segment[:s] + " " + segment[cut:]).strip()
    return None, segment


def _tail_after_product(rest, product):
    """Text after the matched product's name inside `rest` — with no spoken
    separator ('এক কেজি চিনি দুইটা সাবান'), the next item hides there.
    Empty when the product name is not found verbatim (fuzzy match)."""
    names = [product["name_bn"], product["name_en"]] + [
        a.strip() for a in product["aliases"].split(",") if a.strip()
    ]
    end = None
    for n in names:
        idx = rest.find(n)
        if idx >= 0 and (end is None or idx + len(n) > end):
            end = idx + len(n)
    if end is None:
        return ""
    return rest[end:].strip(" ,।")


def _parse_item_segment(conn, segment):
    """One segment like 'দুই কেজি চিনি' or '৫০০ গ্রাম মসুর ডাল' or 'তিনটে সাবান'.
    Returns (item_or_None, ambiguity_or_None, leftover_text, info, tail) where
    info carries qty/stated_price for unknown-item reporting, and tail is any
    further item text hiding after the matched product (no separator)."""
    stated_price, segment = _extract_stated_price(segment)
    phrases = find_number_phrases(segment)
    # A piece-suffixed number ('দুইটা') is not a find_number_phrases phrase,
    # but it can precede a real phrase that belongs to a LATER item
    # ('দুইটা সাবান আধা কেজি মসুর ডাল' — আধা is the third item's qty).
    # Whichever quantity signal starts first owns this segment.
    # NOTE: no \b here — Bengali vowel signs are combining marks, so
    # Python's \b fails after them. Use an explicit lookahead instead.
    pm = re.search(r"(\S+?)(টে|টা|টি|টো|খানা)(?=$|[\s,।])", segment)
    pm_val = None
    if pm:
        # clipped stem: 'দুটা'/'দুটো' is দুইটা with the ই dropped in speech
        pm_val = {"দু": 2.0}.get(pm.group(1))
        if pm_val is None:
            pm_val, _ = parse_number_at([pm.group(1)], 0)
        if pm_val is None:
            pm = None
    qty = None
    qty_span = None
    # A phrase starting IMMEDIATELY after the piece token is the speaker
    # correcting themselves within the same item ('দুটি দুই ডজন ডিম') —
    # the later, fuller signal wins. A phrase after a product word
    # belongs to a later item and does not pre-empt the piece token.
    phrase_first = bool(phrases) and (
        pm is None
        or phrases[0][1] <= pm.start()
        or phrases[0][1] <= pm.end() + 1
    )
    if phrase_first:
        qty, s, e = phrases[0]
        qty_span = (s, e)
        # Stuttered quantity ('দুই দুই ডজন ডিম'): a bare phrase directly
        # followed by another phrase + unit means the first was a false
        # start — advance once to the fuller signal.
        after_first = segment[e:].lstrip()
        sub = find_number_phrases(after_first)
        if sub:
            sv, ss, se = sub[0]
            if ss == 0 and any(
                after_first[se:].lstrip().startswith(uw) for uw in UNITS
            ):
                lead = len(segment[e:]) - len(after_first)
                qty = sv
                qty_span = (e + lead + ss, e + lead + se)
    unit_factor = 1.0
    unit_kind = None
    rest = segment
    if qty_span:
        after = segment[qty_span[1]:].strip()
        for uw, (uk, factor) in sorted(UNITS.items(), key=lambda kv: -len(kv[0])):
            if after.startswith(uw):
                if uw.isascii():
                    # Latin units must end at a word boundary, or 'g'
                    # would match the start of any following word.
                    nxt = after[len(uw): len(uw) + 1]
                    if nxt and nxt not in " ,।":
                        continue
                unit_kind, unit_factor = uk, factor
                rest = (segment[: qty_span[0]] + " " + after[len(uw):]).strip()
                break
        else:
            rest = (segment[: qty_span[0]] + " " + segment[qty_span[1]:]).strip()
    if qty is None and pm is not None:
        qty = pm_val
        unit_kind = "piece"
        rest = (segment[: pm.start()] + " " + segment[pm.end():]).strip()
    matches, ambiguous = search_products(conn, rest)
    if not matches:
        # try dropping filler verbs
        cleaned = re.sub(r"(দাও|দিন|দেন|নাও|করো|করুন|চাই|লিখে রাখো|রাখো|বিল)", "", rest).strip()
        if cleaned != rest:
            matches, ambiguous = search_products(conn, cleaned)
            rest = cleaned
    if len(matches) > 1:
        # Separator-free speech puts several products in one segment
        # ('সাবান এক কেজি চিনি'): the item THIS segment is about is the
        # one whose name occurs first in the text — the rest is the tail.
        # A tie at the same position (বিস্কুট's two packs share one alias)
        # keeps the search order and the ambiguity flag.
        def _occ(p):
            best = None
            names = [p["name_bn"], p["name_en"]] + [
                a.strip() for a in p["aliases"].split(",") if a.strip()
            ]
            for n in names:
                i = rest.find(n)
                if i >= 0 and (best is None or i < best):
                    best = i
            return best

        occs = [(_occ(p), p, s) for p, s in matches]
        with_occ = [o for o in occs if o[0] is not None]
        if with_occ:
            first = min(o[0] for o in with_occ)
            leaders = [o for o in with_occ if o[0] == first]
            if len(leaders) == 1:
                lead = leaders[0]
                matches = [(lead[1], lead[2])] + [
                    (p, s) for (o, p, s) in occs if p["id"] != lead[1]["id"]
                ]
                ambiguous = False
    info = {"qty": qty, "stated_price": stated_price}
    if not matches:
        return None, None, rest, info, ""
    if qty is None and stated_price is None:
        # Residue guard: a fragment carrying no quantity signal of its own
        # must at least name a matched product verbatim. Stripped payment
        # words leave fragments like 'তে' (from 'ইউপিআই তে') that
        # substring-match 'তেল' and would silently bill a litre of oil
        # nobody ordered. A fuzzy name WITH a quantity ('এক লিটার সরষার
        # তেল') is unaffected.
        verbatim = False
        for p, _s in matches:
            names = [p["name_bn"], p["name_en"]] + [
                a.strip() for a in p["aliases"].split(",") if a.strip()
            ]
            if any(n and n in rest for n in names):
                verbatim = True
                break
        if not verbatim:
            return None, None, rest, info, ""
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
            return item, None, rest, info, _tail_after_product(rest, product)
    product, _score = matches[0]
    if stated_price is not None and not ambiguous and product["unit"] in ("kg", "litre"):
        # '৭৫ টাকার চিনি' = 75 taka WORTH of a weight-priced good.
        # `not ambiguous`, not len(matches)==1: a clear-winner search can
        # still return fuzzy extras ('চাল' also fuzzy-matches 'চা পাতা'),
        # and 'এক হাজার টাকার চাল' must be worth-priced on the winner.
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
    return item, amb, rest, info, _tail_after_product(rest, product)


_FILLER = re.compile(
    r"(বিল|করো|করুন|দাও|দিন|দেন|নাও|চাই|রাখো|ইউপিআইতে|ইউপিআই|টাকার|টাকা|দামের"
    r"|টা(?=$|[\s,।])|টে(?=$|[\s,।])|টি(?=$|[\s,।]))"
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
        if not new_name and "জমা" in t:
            # 'জয় সরকার ২০০ টাকা জমা দিলেন' — payer named at the start,
            # with no কে/নামে marker for the other extractor to anchor on.
            new_name = extract_payer_name(conn, text)
        if new_name:
            # not in the customer book yet: carried on the draft, and the
            # ledger creates the customer when the draft is confirmed.
            draft["customer_name"] = new_name

    # due payment: 'রহিমদা ২০০ টাকা জমা দিলেন'
    if "জমা" in t and (customer is not None or draft.get("customer_name")):
        phrases = find_number_phrases(text)
        if phrases:
            draft["kind"] = "due_payment"
            amounts = conflicting_amounts(text)
            if len(amounts) > 1:
                # Two DIFFERENT amounts in one payment sentence are a
                # spoken self-correction ('পাঁচশো টাকা পাঁচশো পঞ্চাশ টাকা
                # জমা দিলো' — spoken batch 3, S5): never silently keep
                # the first. Carry NO amount and ask instead; confirm
                # then fails on its amount guard, so nothing can be
                # saved until the shopkeeper rejects and re-speaks.
                draft["amount_options"] = amounts
                return draft
            draft["amount"] = phrases[0][0]
            return draft

    # due query: 'সুমিতার কত বাকি', 'রহিমের কত টাকা বাকি', 'লক্ষণ সরকারের বাকি কত?'
    if "বাকি" in t and re.search(r"(?:^|\s)(কত|কাদের|কার|হিসাব)(?:\s|$|[?।,])", t):
        draft["kind"] = "due_query"
        is_general = bool(re.search(r"(?:^|\s)(কার|কাদের|মোট|সবাই|সকলের)(?:\s|$)", t))
        if not is_general:
            if customer is not None:
                draft["customer"] = {"id": customer["id"], "name": customer["name"]}
            else:
                m = re.search(r"([\u0980-\u09FF\w\s]+?)(?:-এর|ের|র)?(?:\s+টাকা)?(?:\s+বাকি\s+কত|\s+কত\s+বাকি|\s+কত\s+টাকা\s+বাকি|\s+বাকি)", t)
                if m:
                    target_name = m.group(1).strip()
                    for h in ("বাবুর", "বাবু", "কাকার", "কাকা", "দার", "দা", "ভাই", "দিদি"):
                        if target_name.endswith(h) and len(target_name) > len(h) + 1:
                            target_name = target_name[:-len(h)].strip()
                    if target_name and not re.search(r"(?:^|\s)(কার|কাদের|মোট|বাকি|কত)(?:\s|$)", target_name):
                        cust_match = find_customer(conn, target_name)
                        if cust_match:
                            draft["customer"] = {"id": cust_match["id"], "name": cust_match["name"]}
                        else:
                            draft["customer_name"] = target_name
                            draft["customer_not_found"] = True
        return draft

    # reports
    # 1. specific product sales report: 'আজকে আমুল শক্তি কয়টা বিক্রি হয়েছে?'
    if ("কয়টা" in t or "কয়টা" in t or "কত" in t or "কটি" in t) and ("বিক্রি" in t or "সেল" in t):
        prod_q = re.sub(r"(আজকে|আজ|কয়টা|কয়টা|কত|কটি|বিক্রি|হয়েছে|হলো|শেষ|হিসাব|\?|।)", " ", t)
        prod_q = re.sub(r"\s+", " ", prod_q).strip()
        if prod_q and len(prod_q) >= 2:
            matches, _ = search_products(conn, prod_q)
            if matches and matches[0][1] >= 0.55:
                p = matches[0][0]
                draft["kind"] = "report"
                draft["report"] = "product_sales"
                draft["target_product"] = {"id": p["id"], "name_bn": p["name_bn"], "unit": p["unit"]}
                return draft

    if any(w in t for w in ("বিক্রি কেমন", "কত বিক্রি", "বিক্রি কত")) or (
        "বিক্রি" in t and "কত" in t
    ):
        draft["kind"] = "report"
        draft["report"] = "week_compare" if "সপ্তাহ" in t else "sales_summary"
        return draft
    if "শেষ হয়ে" in t or "কম আছে" in t or "স্টক" in t or ("মাল" in t and "শেষ" in t):
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

    def consume(seg_text, depth=0):
        item, amb, rest, info, tail = _parse_item_segment(conn, seg_text)
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
            return
        # No separator spoken ('এক কেজি চিনি দুইটা সাবান'): the next item
        # hides in the tail after the matched product's name. Only follow
        # it when the tail carries its own quantity — trailing words like
        # a brand name ('সরিষার তেল ফরচুন') are not another item.
        if tail and depth < 3 and (
            find_number_phrases(tail)
            or re.search(r"(\S+?)(টে|টা|টি|টো|খানা)(?=$|[\s,।])", tail)
        ):
            consume(tail, depth + 1)

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
        consume(s.strip())
    draft["items"] = items
    if unknown_items:
        draft["unknown_items"] = unknown_items
    warns = quantity_warnings(items)
    if warns:
        draft["warnings"] = warns
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
        if draft.get("warnings"):
            out += " । ⚠ " + "; ".join(draft["warnings"])
        return out
    if kind == "due_payment":
        name = (draft.get("customer") or {}).get("name") or draft.get("customer_name", "")
        if draft.get("amount") is None:
            opts = " না ".join(fmt_taka(a) for a in draft.get("amount_options", []))
            return f"জমা: {name} — ⚠ দুটো অঙ্ক শোনা গেছে ({opts}) — কোনটা ঠিক? বাতিল করে আবার বলুন"
        return f"জমা: {name} — {fmt_taka(draft.get('amount', 0))}"
    if kind == "undo":
        return "শেষ বিক্রিটা বাতিল করা হবে"
    return kind or ""
