"""Offline tests for the Spark tool layer (Batch G8).

The audit found the DraftSession/dispatch layer had NO tests, so Spark
parity bugs (purchase drafts collapsing into sales, reports answered
as free text) could regress silently. These drive dispatch() directly
with a fake session — no network, no API key.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn, init_db       # noqa: E402
from app.tools import DraftSession, dispatch  # noqa: E402

conn = init_db(get_conn(":memory:"))


def _pid(name_en):
    return conn.execute(
        "SELECT id FROM products WHERE name_en=?", (name_en,)
    ).fetchone()["id"]


def _cid(name):
    return conn.execute(
        "SELECT id FROM customers WHERE name=?", (name,)
    ).fetchone()["id"]


def test_spark_purchase_draft_keeps_its_kind():
    s = DraftSession(conn)
    dispatch(s, "search_product", {"query": "চিনি"})
    dispatch(s, "mark_purchase", {})
    dispatch(s, "add_to_cart", {"product_id": _pid("sugar"), "qty": 5})
    d = s.to_draft("পাঁচ কেজি চিনি কিনলাম")
    assert d["kind"] == "purchase"
    assert d["total"] == 300  # 5 kg x 60 — stock IN, not a sale


def test_spark_sale_stays_sale_without_mark_purchase():
    s = DraftSession(conn)
    dispatch(s, "add_to_cart", {"product_id": _pid("sugar"), "qty": 1})
    assert s.to_draft("এক কেজি চিনি")["kind"] == "sale"


def test_spark_undo_draft():
    s = DraftSession(conn)
    dispatch(s, "request_undo", {})
    assert s.to_draft("শেষ বিক্রিটা বাতিল করো")["kind"] == "undo"


def test_spark_reports_become_structured_kinds():
    # Reports must come back as kind=report so the API answers from the
    # ledger in code — never as LLM-narrated numbers in a reply.
    for tool, args, report in [
        ("get_sales_summary", {"days": 1}, "sales_summary"),
        ("get_low_stock", {}, "low_stock"),
        ("top_products", {"days": 7}, "top_products"),
        ("get_week_compare", {}, "week_compare"),
    ]:
        s = DraftSession(conn)
        dispatch(s, tool, args)
        d = s.to_draft("রিপোর্ট")
        assert d["kind"] == "report" and d["report"] == report, tool


def test_spark_due_query_kind():
    s = DraftSession(conn)
    dispatch(s, "get_customer_due", {"customer_id": _cid("রহিম")})
    d = s.to_draft("রহিমের কত বাকি")
    assert d["kind"] == "due_query"
    assert d["customer"]["name"] == "রহিম"


def test_spark_payment_type_is_validated():
    s = DraftSession(conn)
    r = dispatch(s, "set_payment_type", {"payment_type": "credit"})
    assert "error" in r
    assert s.payment_type == "cash"  # unchanged — "credit" never stored
    dispatch(s, "set_payment_type", {"payment_type": "due"})
    assert s.payment_type == "due"


def test_spark_new_customer_due_payment():
    s = DraftSession(conn)
    dispatch(s, "record_due_payment",
             {"customer_name": "অমিত", "amount": 500})
    d = s.to_draft("অমিত ৫০০ টাকা জমা দিলেন")
    assert d["kind"] == "due_payment"
    assert d["amount"] == 500
    assert d["customer_name"] == "অমিত"


def test_spark_due_payment_two_amounts_in_raw_asks():
    # The model picked 500, but the raw sentence carried a spoken
    # self-correction (500, then 550). to_draft must drop the picked
    # amount and ask — the same guard as the local parser.
    s = DraftSession(conn)
    dispatch(s, "record_due_payment",
             {"customer_name": "সুমিতা", "amount": 500})
    d = s.to_draft("সুমিতা পাঁচশো টাকা পাঁচশো পঞ্চাশ টাকা জমা দিলো")
    assert d["kind"] == "due_payment"
    assert "amount" not in d
    assert d["amount_options"] == [500, 550]


def test_spark_ambiguous_item_stays_flagged():
    s = DraftSession(conn)
    r = dispatch(s, "search_product", {"query": "বিস্কুট"})
    assert r["ambiguous"] is True
    big = r["matches"][0]["product_id"]
    dispatch(s, "add_to_cart", {"product_id": big, "qty": 2})
    d = s.to_draft("দুইটা বিস্কুট")
    assert d["ambiguities"], "Spark draft must carry the ambiguity"
    assert d["items"][0]["ambiguous"] is True


def test_spark_unknown_item_is_flagged_not_dropped():
    s = DraftSession(conn)
    dispatch(s, "search_product", {"query": "ম্যাগি"})
    d = s.to_draft("একটা ম্যাগি")
    assert any(u["text"] == "ম্যাগি" for u in d.get("unknown_items", []))


def test_unlisted_tool_is_rejected():
    s = DraftSession(conn)
    r = dispatch(s, "run_sql", {"q": "DELETE FROM sales"})
    assert "error" in r


# --- The full understand() loop, driven by a stub model (still offline) ---

class _Fn:
    def __init__(self, name, args):
        self.name = name
        self.arguments = args


class _Call:
    def __init__(self, i, name, args):
        self.id = f"call{i}"
        self.function = _Fn(name, args)


class _Msg:
    def __init__(self, tool_calls=None, content=""):
        self.tool_calls = tool_calls
        self.content = content


class _Resp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]


class _StubCompletions:
    """Plays a scripted model: first the given tool calls, then a final
    plain reply — exactly how Spark drives the session."""

    def __init__(self, calls):
        self._calls = calls
        self._turn = 0

    def create(self, model, messages, tools):
        self._turn += 1
        if self._turn == 1:
            return _Resp(_Msg(tool_calls=[
                _Call(i, n, a) for i, (n, a) in enumerate(self._calls)
            ]))
        return _Resp(_Msg(content="হয়ে গেছে"))


def _understand_with(calls, text):
    import json as _json

    from app.meta_client import MetaClient

    mc = MetaClient()
    stub = type("Stub", (), {})()
    stub.chat = type("Chat", (), {
        "completions": _StubCompletions([
            (n, _json.dumps(a)) for n, a in calls
        ]),
    })()
    mc._get = lambda: stub
    return mc.understand(conn, text)


def test_understand_loop_builds_purchase_draft():
    d = _understand_with([
        ("mark_purchase", {}),
        ("search_product", {"query": "চিনি"}),
        ("add_to_cart", {"product_id": _pid("sugar"), "qty": 5}),
    ], "পাঁচ কেজি চিনি কিনলাম")
    assert d["kind"] == "purchase" and d["total"] == 300


def test_understand_loop_report_is_structured_not_narrated():
    d = _understand_with([
        ("get_sales_summary", {"days": 1}),
    ], "আজকের বিক্রি কত")
    assert d["kind"] == "report" and d["report"] == "sales_summary"


def test_understand_loop_undo():
    d = _understand_with([("request_undo", {})], "শেষ বিক্রিটা বাতিল করো")
    assert d["kind"] == "undo"
