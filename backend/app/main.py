"""comam AI API.

Flow: POST /api/command turns a Bengali sentence (typed, or transcribed
from voice) into a DRAFT. Nothing reaches the ledger until
POST /api/drafts/{id}/confirm — the shopkeeper always confirms first.
Reports and due-queries are read-only and answered immediately.
"""
import json
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bn import fmt_num, fmt_taka, format_speech_bn
from .db import get_conn, init_db
from .ledger import Ledger
from .meta_client import MetaClient
from .parser_local import draft_summary_bn, parse_command

import os

if not os.environ.get("TESTING") and not os.environ.get("PYTEST_CURRENT_TEST"):
    try:
        # Local development: MODEL_API_KEY etc. live in the repo-root .env.
        # Optional — in production the environment provides them directly.
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        load_dotenv()
    except ImportError:
        pass

app = FastAPI(title="comam AI")
meta = MetaClient()
_conn = None
# One shared SQLite connection => draft mutations (confirm / reject /
# resolve / items) are serialised per process. Together with the
# claim-first status update in confirm, a draft can never be
# confirmed twice, even by two concurrent requests (FIX-PLAN-2 D4).
_draft_lock = threading.Lock()


def conn():
    global _conn
    if _conn is None:
        _conn = init_db(get_conn())
    return _conn


def ledger():
    return Ledger(conn())


class CommandIn(BaseModel):
    text: str


def _save_draft(draft):
    cur = conn().execute(
        "INSERT INTO drafts (kind, payload) VALUES (?,?)",
        (draft["kind"], json.dumps(draft, ensure_ascii=False)),
    )
    conn().commit()
    return cur.lastrowid


def _report_answer(report, led: Ledger, draft: dict = None):
    if report == "product_sales":
        tp = draft.get("target_product") if draft else None
        if not tp:
            return "কোন পণ্যের বিক্রয় জানতে চান তা নির্দিষ্ট করা যায়নি।", {}
        data = led.product_sales(tp["id"], days=1)
        if not data:
            return f"{tp.get('name_bn', 'পণ্য')}-এর কোনো তথ্য পাওয়া যায়নি।", {}
        sold = data["sold_qty"]
        tot = data["sales_total"]
        stock = data["current_stock"]
        unit = data.get("unit", "")
        ans = f"{data['name_bn']} আজ {fmt_num(sold)} {unit} বিক্রি হয়েছে (মোট {fmt_taka(tot)})। বর্তমান স্টক: {fmt_num(stock)} {unit}।"
        return ans, data
    if report == "sales_summary":
        s = led.sales_summary(1, calendar=True)
        return (f"আজ মোট {fmt_taka(s['sales_total'])} বিক্রি হয়েছে "
                f"({fmt_num(s['sales_count'])}টা বিল), আনুমানিক লাভ {fmt_taka(s['est_profit'])}।"), s
    if report == "week_compare":
        w = led.week_compare()
        # Honest label (FIX-PLAN-2 E2): this is a rolling last-7-days
        # vs the 7 days before, not a calendar week — say so.
        base = (f"শেষ ৭ দিনে বিক্রি {fmt_taka(w['this_week'])}, তার আগের ৭ দিনে {fmt_taka(w['last_week'])}।")
        if w["change_pct"] is not None:
            base += f" আগের ৭ দিনের চেয়ে {fmt_num(abs(w['change_pct']))}% {'বেশি' if w['change_pct'] >= 0 else 'কম'}।"
        return base, w
    if report == "low_stock":
        items = led.low_stock()
        if not items:
            return "সব পণ্যের স্টক ঠিকঠাক আছে।", {"low_stock": []}
        names = ", ".join(f"{i['name_bn']} ({fmt_num(i['stock'])} {i['unit']})" for i in items)
        return f"শেষ হয়ে আসছে: {names}।", {"low_stock": items}
    if report == "top_products":
        top = led.top_products(7)
        if not top:
            return "গত ৭ দিনে এখনো কোনো বিক্রি হয়নি।", {"top": []}
        names = ", ".join(f"{t['name_bn']} ({fmt_taka(t['amount'])})" for t in top)
        return f"গত ৭ দিনে সবচেয়ে বেশি বিক্রি: {names}।", {"top": top}
    return "", {}


@app.get("/api/products")
def products():
    rows = conn().execute("SELECT * FROM products ORDER BY name_bn").fetchall()
    # The purchase cost is the shop's margin secret (Batch G6): the
    # frontend never displays it, so it does not leave the server.
    return [{k: v for k, v in dict(r).items() if k != "cost"} for r in rows]


class CustomerCreateIn(BaseModel):
    name: str
    phone: str = ""
    aliases: str = ""


@app.post("/api/customers")
def create_customer(body: CustomerCreateIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Customer name cannot be empty")
    c = ledger().create_customer(name, phone=body.phone, aliases=body.aliases)
    return dict(c)


@app.get("/api/customers")
def customers():
    out = []
    led = ledger()
    for c in conn().execute("SELECT * FROM customers").fetchall():
        d = dict(c)
        d["due"] = led.customer_due(c["id"])
        out.append(d)
    return out


@app.get("/api/dues")
def dues():
    return ledger().all_dues()


@app.post("/api/command")
def command(body: CommandIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty command")
    draft = None
    if meta.enabled:
        try:
            draft = meta.understand(conn(), text)
            draft.setdefault("engine", "spark")
        except Exception:
            draft = None  # fall back to the deterministic parser
    if draft is None:
        draft = parse_command(conn(), text)
        draft["engine"] = "local"
    kind = draft["kind"]
    if kind == "due_query":
        if draft.get("customer_not_found"):
            c_name = draft.get("customer_name") or "উক্ত"
            answer = f'"{c_name}" নামে কোনো গ্রাহক খুঁজে পাওয়া যায়নি।'
        elif draft.get("customer"):
            due = ledger().customer_due(draft["customer"]["id"])
            answer = f"{draft['customer']['name']}-এর বাকি {fmt_taka(due)}।"
        else:
            all_d = ledger().all_dues()
            answer = ("কারো বাকি নেই।" if not all_d else
                      "বাকি আছে: " + ", ".join(f"{d['customer']} {fmt_taka(d['due'])}" for d in all_d) + "।")
        speech = format_speech_bn(answer)
        return {"kind": kind, "answer_bn": answer, "answer_speech_bn": speech, "draft": draft}
    if kind == "report":
        answer, data = _report_answer(draft["report"], ledger(), draft)
        speech = format_speech_bn(answer)
        return {"kind": kind, "answer_bn": answer, "answer_speech_bn": speech, "data": data, "draft": draft}
    if kind == "unknown":
        reply = draft.get("reply") or "বুঝতে পারিনি — আরেকবার বলবেন?"
        speech = format_speech_bn(reply)
        return {
            "kind": kind,
            "answer_bn": reply,
            "answer_speech_bn": speech,
            "draft": draft,
        }
    if kind == "undo":
        # Pin the target NOW: the sale the shopkeeper means is the last
        # one at draft time, not whatever is last when they confirm.
        last = ledger().last_sale()
        if last:
            draft["target_sale_id"] = last["id"]
    draft_id = _save_draft(draft)
    return {
        "kind": kind,
        "draft_id": draft_id,
        "summary_bn": draft_summary_bn(draft),
        "draft": draft,
        "needs_clarification": bool(
            draft.get("ambiguities") or draft.get("amount_options")
        ),
    }


@app.post("/api/drafts/{draft_id}/confirm")
def confirm_draft(draft_id: int):
    with _draft_lock:
        return _confirm_draft_locked(draft_id)


def _confirm_draft_locked(draft_id: int):
    row = conn().execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if row["status"] != "pending":
        raise HTTPException(400, "draft already handled")
    draft = json.loads(row["payload"])
    led = ledger()
    result = {}
    # Claim the draft BEFORE any ledger effect (FIX-PLAN-2 D4): the
    # conditional UPDATE only succeeds for the first confirmer. If a
    # later guard or effect fails, the claim is reverted so the draft
    # stays pending and usable.
    cur = conn().execute(
        "UPDATE drafts SET status='confirmed' WHERE id=? AND status='pending'",
        (draft_id,),
    )
    if cur.rowcount == 0:
        raise HTTPException(400, "draft already handled")
    conn().commit()
    try:
        if draft["kind"] == "sale":
            if draft.get("ambiguities"):
                raise HTTPException(400, "resolve ambiguities first")
            if draft.get("unknown_items"):
                names = ", ".join(u["text"] for u in draft["unknown_items"])
                raise HTTPException(
                    400, f"ক্যাটালগে নেই এমন পণ্য আছে: {names} — আগে ঠিক করুন"
                )
            # Money guards — they live here, at confirm, so BOTH engines
            # (local parser and Spark tool-calling) are held to them.
            if not draft.get("items"):
                raise HTTPException(400, "কোনো পণ্য নেই — খালি বিল সেভ হবে না")
            for i in draft["items"]:
                if float(i["qty"]) <= 0:
                    raise HTTPException(400, "পরিমাণ শূন্যের বেশি হতে হবে")
            # An out-of-vocabulary payment type (Batch G3) must never be
            # stored: the due ledger only counts "due", so a sale saved
            # as e.g. "credit" would vanish from every total.
            if draft.get("payment_type", "cash") not in ("cash", "due", "upi"):
                raise HTTPException(400, "অজানা পেমেন্ট ধরন — নগদ, বাকি বা ইউপিআই হতে হবে")
            customer_id = (draft.get("customer") or {}).get("id")
            if customer_id is None and draft.get("customer_name"):
                customer_id = led.get_or_create_customer(draft["customer_name"])["id"]
            if draft.get("payment_type", "cash") == "due" and customer_id is None:
                raise HTTPException(
                    400, "বাকি বিক্রির জন্য ক্রেতা দরকার — কার নামে বাকি রাখব?"
                )
            # Stock is checked against the AGGREGATED quantity per
            # product (Batch G1): the same product on two lines used to
            # pass line-by-line and drive the stock negative.
            wanted = {}
            for i in draft["items"]:
                wanted[i["product_id"]] = wanted.get(i["product_id"], 0.0) + float(i["qty"])
            for pid, qty in wanted.items():
                p = conn().execute(
                    "SELECT stock, name_bn, unit FROM products WHERE id=?",
                    (pid,),
                ).fetchone()
                if p is not None and qty > float(p["stock"]):
                    raise HTTPException(
                        400,
                        f"স্টকে {p['name_bn']} আছে {fmt_num(p['stock'])} {p['unit']}"
                        " — এর বেশি বিক্রি হবে না",
                    )
            try:
                sale = led.create_sale(
                    [{"product_id": i["product_id"], "qty": i["qty"]} for i in draft["items"]],
                    customer_id=customer_id,
                    payment_type=draft.get("payment_type", "cash"),
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            result = {"sale": sale}
        elif draft["kind"] == "purchase":
            # Same guards as a sale (FIX-PLAN-2 D1): a purchase the parser
            # flagged as ambiguous or unknown must never stock a guessed item.
            if draft.get("ambiguities"):
                raise HTTPException(400, "resolve ambiguities first")
            if draft.get("unknown_items"):
                names = ", ".join(u["text"] for u in draft["unknown_items"])
                raise HTTPException(
                    400, f"ক্যাটালগে নেই এমন পণ্য আছে: {names} — আগে ঠিক করুন"
                )
            if not draft.get("items"):
                raise HTTPException(400, "কোনো পণ্য নেই — খালি ক্রয় সেভ হবে না")
            for i in draft["items"]:
                if float(i["qty"]) <= 0:
                    raise HTTPException(400, "পরিমাণ শূন্যের বেশি হতে হবে")
            # One transaction for the whole purchase (FIX-PLAN-2 D2): a bad
            # product id anywhere means nothing is stocked, not half of it.
            try:
                led.record_purchases(
                    [{"product_id": i["product_id"], "qty": i["qty"]} for i in draft["items"]]
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            result = {"purchased": draft["items"]}
        elif draft["kind"] == "due_payment":
            customer_id = (draft.get("customer") or {}).get("id")
            if customer_id is None and draft.get("customer_name"):
                customer_id = led.get_or_create_customer(draft["customer_name"])["id"]
            if customer_id is None:
                raise HTTPException(400, "customer required for due payment")
            if draft.get("amount_options"):
                # Two different amounts were heard (a self-correction):
                # the draft deliberately carries no amount — ask, never
                # guess. The shopkeeper rejects and re-speaks.
                opts = " না ".join(fmt_taka(a) for a in draft["amount_options"])
                raise HTTPException(
                    400, f"দুটো অঙ্ক শোনা গেছে ({opts}) — কোনটা ঠিক? বাতিল করে আবার বলুন"
                )
            amount = float(draft.get("amount") or 0)
            if amount <= 0:
                raise HTTPException(400, "জমার পরিমাণ শূন্যের বেশি হতে হবে")
            due_now = led.customer_due(customer_id)
            if amount > due_now:
                raise HTTPException(
                    400, f"বাকি আছে মাত্র {fmt_taka(due_now)} — এর বেশি জমা নেওয়া যাবে না"
                )
            due = led.record_due_payment(customer_id, amount)
            result = {"remaining_due": due}
        elif draft["kind"] == "undo":
            target = draft.get("target_sale_id")
            try:
                voided = led.void_sale(target) if target else led.void_last_sale()
            except ValueError as e:
                raise HTTPException(400, str(e))
            if voided is None:
                raise HTTPException(400, "nothing to undo")
            result = {"voided_sale": voided}
        else:
            raise HTTPException(400, f"cannot confirm kind {draft['kind']}")
        conn().commit()
        return {"ok": True, "result": result}
    except Exception:
        conn().execute(
            "UPDATE drafts SET status='pending' WHERE id=? AND status='confirmed'",
            (draft_id,),
        )
        conn().commit()
        raise


@app.post("/api/drafts/{draft_id}/reject")
def reject_draft(draft_id: int):
    with _draft_lock:
        return _reject_draft_locked(draft_id)


def _reject_draft_locked(draft_id: int):
    row = conn().execute(
        "SELECT status FROM drafts WHERE id=?", (draft_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if row["status"] != "pending":
        raise HTTPException(400, "draft already handled")
    cur = conn().execute(
        "UPDATE drafts SET status='rejected' WHERE id=? AND status='pending'",
        (draft_id,),
    )
    if cur.rowcount == 0:
        raise HTTPException(400, "draft already handled")
    conn().commit()
    return {"ok": True}


@app.get("/api/reports/summary")
def report_summary(days: int = 1):
    # One meaning of "আজ" everywhere (Batch G7): days=1 is the calendar
    # day, exactly like the voice answer — a rolling 24h window used to
    # make the dashboard card disagree with the spoken answer.
    if days == 1:
        return ledger().sales_summary(1, calendar=True)
    return ledger().sales_summary(days)


@app.get("/api/reports/week")
def report_week():
    return ledger().week_compare()


@app.get("/api/reports/top")
def report_top(days: int = 7):
    return ledger().top_products(days)


@app.get("/api/reports/low-stock")
def report_low_stock():
    return ledger().low_stock()


class ResolveIn(BaseModel):
    product_id: int
    ambiguity_index: int | None = None


class ItemIn(BaseModel):
    product_id: int
    qty: float


class ItemsIn(BaseModel):
    # Typed items (Batch G5): a missing qty or a non-numeric qty is a
    # 422 from Pydantic, not a 500 from a KeyError deep in the rebuild.
    items: list[ItemIn]
    # Dropping catalog-unknown items must be an explicit act, not a
    # side effect of any edit (FIX-PLAN-2 D5).
    acknowledge_unknown: bool = False


def _load_draft(draft_id: int):
    row = conn().execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if row["status"] != "pending":
        raise HTTPException(400, "draft already handled")
    return json.loads(row["payload"])


def _store_draft(draft_id: int, draft):
    conn().execute(
        "UPDATE drafts SET payload=? WHERE id=?",
        (json.dumps(draft, ensure_ascii=False), draft_id),
    )
    conn().commit()


def _rebuild_items(raw_items):
    """Recompute item rows from DB prices (client/parser prices never trusted)."""
    out = []
    for it in raw_items:
        p = conn().execute(
            "SELECT * FROM products WHERE id=?", (it["product_id"],)
        ).fetchone()
        if p is None:
            raise HTTPException(400, f"unknown product {it['product_id']}")
        qty = float(it["qty"])
        if qty <= 0:
            continue
        out.append(
            {
                "product_id": p["id"],
                "name_bn": p["name_bn"],
                "qty": qty,
                "unit": p["unit"],
                "unit_price": p["price"],
                "line_total": round(p["price"] * qty, 2),
            }
        )
    return out


@app.post("/api/drafts/{draft_id}/resolve")
def resolve_draft(draft_id: int, body: ResolveIn):
    """Shopkeeper picked one option for ONE ambiguous item. Only that
    ambiguity is settled — any others stay open and keep confirm blocked."""
    draft = _load_draft(draft_id)
    ambs = draft.get("ambiguities", [])
    if not ambs:
        raise HTTPException(400, "no ambiguity in this draft")
    idx = body.ambiguity_index if body.ambiguity_index is not None else 0
    if idx < 0 or idx >= len(ambs):
        raise HTTPException(400, "ambiguity index out of range")
    amb_items = [it for it in draft.get("items", []) if it.get("ambiguous")]
    if idx >= len(amb_items):
        raise HTTPException(400, "ambiguous item not found")
    # Which cart item does this ambiguity belong to? Prefer the item at
    # the same position — when two lines are ambiguous with IDENTICAL
    # option sets (two biscuit lines), an option-set match alone always
    # picks the first line and silently rewrites the wrong item
    # (Batch G8). Fall back to the option-set match for the Spark
    # engine, where search order and cart order need not agree.
    want = {o["product_id"] for o in ambs[idx].get("options", [])}
    positional = amb_items[idx]
    if {o["product_id"] for o in positional.get("options", [])} == want:
        target = positional
    else:
        same_options = [
            it for it in amb_items
            if {o["product_id"] for o in it.get("options", [])} == want
        ]
        target = same_options[0] if same_options else positional
    if body.product_id not in [o["product_id"] for o in target.get("options", [])]:
        raise HTTPException(400, "choice is not one of the offered options")
    rebuilt = _rebuild_items([{"product_id": body.product_id, "qty": target["qty"]}])
    if not rebuilt:
        raise HTTPException(400, "invalid choice")
    pos = draft["items"].index(target)
    draft["items"][pos] = rebuilt[0]
    draft["total"] = round(sum(i["line_total"] for i in draft["items"]), 2)
    draft["ambiguities"] = [a for j, a in enumerate(ambs) if j != idx]
    _store_draft(draft_id, draft)
    return {"draft": draft, "summary_bn": draft_summary_bn(draft)}


@app.put("/api/drafts/{draft_id}/items")
def edit_draft_items(draft_id: int, body: ItemsIn):
    """Shopkeeper edits quantities (or removes items with qty 0) before
    confirming — the draft is always editable, totals recomputed in code."""
    draft = _load_draft(draft_id)
    if draft["kind"] not in ("sale", "purchase"):
        raise HTTPException(400, "only sale/purchase drafts have items")
    draft["items"] = _rebuild_items([i.model_dump() for i in body.items])
    # Catalog-unknown items are settled ONLY by an explicit
    # acknowledgement (FIX-PLAN-2 D5): a plain edit leaves the list —
    # and the confirm block — in place, so the ⚠ can never be
    # clicked past by accident.
    if body.acknowledge_unknown:
        draft.pop("unknown_items", None)
    draft["total"] = round(sum(i["line_total"] for i in draft["items"]), 2)
    _store_draft(draft_id, draft)
    return {"draft": draft, "summary_bn": draft_summary_bn(draft)}


@app.get("/api/health")
def health():
    return {"ok": True, "spark_enabled": meta.enabled}


@app.get("/api/reports/daily")
def report_daily(days: int = 7):
    return ledger().daily_sales(days)


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not meta.enabled:
        raise HTTPException(501, "MODEL_API_KEY not set on server")
    data = await file.read()
    try:
        text = meta.transcribe(data, file.filename or "audio.webm")
    except Exception as e:  # model id/format unverified until Day-1 test
        raise HTTPException(502, f"transcription failed: {e}")
    return {"text": text}


# Serve the built frontend when present (frontend/dist).
DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")
