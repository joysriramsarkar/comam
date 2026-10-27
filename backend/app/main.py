"""comam AI API.

Flow: POST /api/command turns a Bengali sentence (typed, or transcribed
from voice) into a DRAFT. Nothing reaches the ledger until
POST /api/drafts/{id}/confirm — the shopkeeper always confirms first.
Reports and due-queries are read-only and answered immediately.
"""
import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bn import fmt_num, fmt_taka
from .db import get_conn, init_db
from .ledger import Ledger
from .meta_client import MetaClient
from .parser_local import draft_summary_bn, parse_command

app = FastAPI(title="comam AI")
meta = MetaClient()
_conn = None


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


def _report_answer(report, led: Ledger):
    if report == "sales_summary":
        s = led.sales_summary(1)
        return (f"আজ মোট {fmt_taka(s['sales_total'])} বিক্রি হয়েছে "
                f"({fmt_num(s['sales_count'])}টা বিল), আনুমানিক লাভ {fmt_taka(s['est_profit'])}।"), s
    if report == "week_compare":
        w = led.week_compare()
        base = (f"এই সপ্তাহে বিক্রি {fmt_taka(w['this_week'])}, গত সপ্তাহে {fmt_taka(w['last_week'])}।")
        if w["change_pct"] is not None:
            base += f" আগের সপ্তাহের চেয়ে {fmt_num(w['change_pct'])}% {'বেশি' if w['change_pct'] >= 0 else 'কম'}।"
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
    return [dict(r) for r in rows]


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
        if draft.get("customer"):
            due = ledger().customer_due(draft["customer"]["id"])
            answer = f"{draft['customer']['name']}-এর বাকি {fmt_taka(due)}।"
        else:
            all_d = ledger().all_dues()
            answer = ("কারো বাকি নেই।" if not all_d else
                      "বাকি আছে: " + ", ".join(f"{d['customer']} {fmt_taka(d['due'])}" for d in all_d) + "।")
        return {"kind": kind, "answer_bn": answer, "draft": draft}
    if kind == "report":
        answer, data = _report_answer(draft["report"], ledger())
        return {"kind": kind, "answer_bn": answer, "data": data, "draft": draft}
    if kind == "unknown":
        return {"kind": kind, "answer_bn": "বুঝতে পারিনি — আরেকবার বলবেন?", "draft": draft}
    draft_id = _save_draft(draft)
    return {
        "kind": kind,
        "draft_id": draft_id,
        "summary_bn": draft_summary_bn(draft),
        "draft": draft,
        "needs_clarification": bool(draft.get("ambiguities")),
    }


@app.post("/api/drafts/{draft_id}/confirm")
def confirm_draft(draft_id: int):
    row = conn().execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "draft not found")
    if row["status"] != "pending":
        raise HTTPException(400, "draft already handled")
    draft = json.loads(row["payload"])
    led = ledger()
    result = {}
    if draft["kind"] == "sale":
        if draft.get("ambiguities"):
            raise HTTPException(400, "resolve ambiguities first")
        sale = led.create_sale(
            [{"product_id": i["product_id"], "qty": i["qty"]} for i in draft["items"]],
            customer_id=(draft.get("customer") or {}).get("id"),
            payment_type=draft.get("payment_type", "cash"),
        )
        result = {"sale": sale}
    elif draft["kind"] == "purchase":
        for i in draft["items"]:
            led.record_purchase(i["product_id"], i["qty"])
        result = {"purchased": draft["items"]}
    elif draft["kind"] == "due_payment":
        due = led.record_due_payment(draft["customer"]["id"], draft["amount"])
        result = {"remaining_due": due}
    elif draft["kind"] == "undo":
        voided = led.void_last_sale()
        if voided is None:
            raise HTTPException(400, "nothing to undo")
        result = {"voided_sale": voided}
    else:
        raise HTTPException(400, f"cannot confirm kind {draft['kind']}")
    conn().execute("UPDATE drafts SET status='confirmed' WHERE id=?", (draft_id,))
    conn().commit()
    return {"ok": True, "result": result}


@app.post("/api/drafts/{draft_id}/reject")
def reject_draft(draft_id: int):
    conn().execute("UPDATE drafts SET status='rejected' WHERE id=?", (draft_id,))
    conn().commit()
    return {"ok": True}


@app.get("/api/reports/summary")
def report_summary(days: int = 1):
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


@app.post("/api/undo-last-sale")
def undo_last_sale():
    voided = ledger().void_last_sale()
    if voided is None:
        raise HTTPException(400, "nothing to undo")
    return {"voided": voided}


class ResolveIn(BaseModel):
    product_id: int


class ItemsIn(BaseModel):
    items: list[dict]


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
    """Shopkeeper picked one option for an ambiguous item. Swap it in,
    recompute, and unblock confirmation."""
    draft = _load_draft(draft_id)
    target = None
    for it in draft.get("items", []):
        if it.get("ambiguous"):
            target = it
            break
    if target is None:
        raise HTTPException(400, "no ambiguous item in this draft")
    rebuilt = _rebuild_items([{"product_id": body.product_id, "qty": target["qty"]}])
    if not rebuilt:
        raise HTTPException(400, "invalid choice")
    idx = draft["items"].index(target)
    draft["items"][idx] = rebuilt[0]
    draft["items"] = _rebuild_items(draft["items"])
    draft["total"] = round(sum(i["line_total"] for i in draft["items"]), 2)
    draft["ambiguities"] = []
    _store_draft(draft_id, draft)
    return {"draft": draft, "summary_bn": draft_summary_bn(draft)}


@app.put("/api/drafts/{draft_id}/items")
def edit_draft_items(draft_id: int, body: ItemsIn):
    """Shopkeeper edits quantities (or removes items with qty 0) before
    confirming — the draft is always editable, totals recomputed in code."""
    draft = _load_draft(draft_id)
    if draft["kind"] not in ("sale", "purchase"):
        raise HTTPException(400, "only sale/purchase drafts have items")
    draft["items"] = _rebuild_items(body.items)
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
