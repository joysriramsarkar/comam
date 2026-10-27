import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod           # noqa: E402
from app.db import get_conn, init_db       # noqa: E402

main_mod._conn = init_db(get_conn(":memory:"))
client = TestClient(main_mod.app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_draft_confirm_flow():
    r = client.post("/api/command", json={"text": "দুই কেজি চিনি নগদ"})
    d = r.json()
    assert d["kind"] == "sale" and d["draft"]["total"] == 120
    stock_before = [p for p in client.get("/api/products").json() if p["name_en"] == "sugar"][0]["stock"]
    r2 = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert r2.status_code == 200
    stock_after = [p for p in client.get("/api/products").json() if p["name_en"] == "sugar"][0]["stock"]
    assert stock_after == stock_before - 2


def test_ambiguous_resolve_then_confirm():
    r = client.post("/api/command", json={"text": "দুইটা বিস্কুট দাও"})
    d = r.json()
    assert d["needs_clarification"] is True
    blocked = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert blocked.status_code == 400
    small = [o for o in d["draft"]["ambiguities"][0]["options"] if o["price"] == 10][0]
    r2 = client.post(f"/api/drafts/{d['draft_id']}/resolve", json={"product_id": small["product_id"]})
    assert r2.json()["draft"]["total"] == 20  # 2 x 10
    ok = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert ok.status_code == 200


def test_edit_items_recomputes_total():
    r = client.post("/api/command", json={"text": "এক কেজি আলু"})
    d = r.json()
    item = d["draft"]["items"][0]
    r2 = client.put(f"/api/drafts/{d['draft_id']}/items",
                    json={"items": [{"product_id": item["product_id"], "qty": 4}]})
    assert r2.json()["draft"]["total"] == 120  # 4 kg x 30


def test_reports_endpoints():
    assert client.get("/api/reports/summary?days=1").status_code == 200
    daily = client.get("/api/reports/daily?days=7").json()
    assert len(daily) == 7 and "total" in daily[-1]
    assert client.get("/api/reports/low-stock").status_code == 200


def test_new_customer_upi_flow_and_unknown_block():
    text = (
        "জয় সরকারকে তিনটা ডিম, একটা পনেরো টাকার ম্যাগি আর একটা দশ টাকা দামের "
        "ফার্মের বিস্কুট বিল করো। ইউপিআইতে।"
    )
    r = client.post("/api/command", json={"text": text})
    d = r.json()
    assert d["draft"]["customer_name"] == "জয় সরকার"
    assert d["draft"]["payment_type"] == "upi"
    # confirm is blocked while a catalog-unknown item is unresolved
    r2 = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert r2.status_code == 400
    # a plain item edit does NOT settle the warning (FIX-PLAN-2 D5):
    # dropping the unknown product must be an explicit acknowledgement
    items = [
        {"product_id": i["product_id"], "qty": i["qty"]}
        for i in d["draft"]["items"]
    ]
    r3 = client.put(f"/api/drafts/{d['draft_id']}/items", json={"items": items})
    assert r3.status_code == 200
    assert r3.json()["draft"].get("unknown_items"), "edit alone must keep the warning"
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 400
    # with the explicit acknowledgement the warning settles and the
    # unknown item is dropped loudly — never billed (total stays 31)
    r3b = client.put(
        f"/api/drafts/{d['draft_id']}/items",
        json={"items": items, "acknowledge_unknown": True},
    )
    assert not r3b.json()["draft"].get("unknown_items")
    r4 = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert r4.status_code == 200
    sale = r4.json()["result"]["sale"]
    assert sale["payment_type"] == "upi" and sale["total"] == 31
    names = [c["name"] for c in client.get("/api/customers").json()]
    assert "জয় সরকার" in names


def _cmd(text):
    return client.post("/api/command", json={"text": text}).json()


def test_confirm_guards_money():
    # due sale without a customer: the money would be tracked nowhere
    d = _cmd("দুই কেজি চিনি বাকিতে")
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 400
    # oversell beyond stock
    d = _cmd("একশো কেজি চিনি নগদ")
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 400
    # empty sale (items edited away)
    d = _cmd("এক কেজি চিনি নগদ")
    client.put(f"/api/drafts/{d['draft_id']}/items", json={"items": []})
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 400


def test_overpayment_blocked_exact_payment_ok():
    d = _cmd("দুই কেজি চিনি করিমদার নামে বাকিতে")
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 200
    d = _cmd("করিমদা তিনশো টাকা জমা দিলেন")
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 400
    d = _cmd("করিমদা একশো বিশ টাকা জমা দিলেন")
    r = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert r.status_code == 200 and r.json()["result"]["remaining_due"] == 0


def test_reject_semantics():
    assert client.post("/api/drafts/9999/reject").status_code == 404
    d = _cmd("এক কেজি চিনি নগদ")
    assert client.post(f"/api/drafts/{d['draft_id']}/reject").status_code == 200
    assert client.post(f"/api/drafts/{d['draft_id']}/reject").status_code == 400


def test_two_ambiguities_resolve_one_by_one():
    d = _cmd("দুইটা বিস্কুট আর একটা বিস্কুট")
    did = d["draft_id"]
    r = client.post(f"/api/drafts/{did}/resolve",
                    json={"product_id": 11, "ambiguity_index": 0})
    assert r.status_code == 200
    assert len(r.json()["draft"]["ambiguities"]) == 1
    # still blocked: the second item is still a guess
    assert client.post(f"/api/drafts/{did}/confirm").status_code == 400
    r = client.post(f"/api/drafts/{did}/resolve",
                    json={"product_id": 10, "ambiguity_index": 0})
    assert r.status_code == 200
    assert client.post(f"/api/drafts/{did}/confirm").status_code == 200


def test_undo_voids_the_sale_from_draft_time():
    d1 = _cmd("এক কেজি চিনি নগদ")
    s1 = client.post(f"/api/drafts/{d1['draft_id']}/confirm").json()["result"]["sale"]["id"]
    du = _cmd("শেষ বিক্রিটা বাতিল করো")
    d2 = _cmd("এক কেজি চাল নগদ")
    s2 = client.post(f"/api/drafts/{d2['draft_id']}/confirm").json()["result"]["sale"]["id"]
    r = client.post(f"/api/drafts/{du['draft_id']}/confirm")
    assert r.status_code == 200
    assert r.json()["result"]["voided_sale"]["id"] == s1 != s2


def test_ambiguous_purchase_confirm_blocked():
    # FIX-PLAN-2 D1 (probe V1): the purchase branch must refuse an
    # ambiguous draft exactly like the sale branch — never stock a guess.
    def biscuit_stocks():
        return {p["name_bn"]: p["stock"]
                for p in client.get("/api/products").json()
                if "বিস্কুট" in p["name_bn"]}

    before = biscuit_stocks()
    d = _cmd("দুইটা বিস্কুট কিনলাম")
    assert d["kind"] == "purchase" and d["needs_clarification"] is True
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 400
    assert biscuit_stocks() == before
    # the refused confirm left the draft pending (claim reverted, D4):
    # resolving the ambiguity lets the SAME draft confirm cleanly.
    small = [o for o in d["draft"]["ambiguities"][0]["options"] if o["price"] == 10][0]
    client.post(f"/api/drafts/{d['draft_id']}/resolve",
                json={"product_id": small["product_id"]})
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 200
    assert biscuit_stocks()["বিস্কুট ছোট প্যাকেট"] == before["বিস্কুট ছোট প্যাকেট"] + 2


def test_double_confirm_moves_stock_once():
    # FIX-PLAN-2 D4: a second confirm is refused and stock moves once.
    d = _cmd("এক কেজি আলু নগদ")
    before = [p for p in client.get("/api/products").json()
              if p["name_en"] == "potato"][0]["stock"]
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 200
    assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 400
    after = [p for p in client.get("/api/products").json()
             if p["name_en"] == "potato"][0]["stock"]
    assert after == before - 1


def test_week_answer_says_last_7_days():
    # FIX-PLAN-2 E2: rolling windows must be labelled as what they are.
    r = client.post("/api/command",
                    json={"text": "গত সপ্তাহের চেয়ে এই সপ্তাহে বিক্রি কেমন"})
    assert r.json()["kind"] == "report"
    assert "শেষ ৭ দিনে" in r.json()["answer_bn"]


def test_concurrent_confirm_exactly_once():
    # FIX-PLAN-2 D4 race probe: 4 simultaneous confirms of one draft —
    # exactly one may succeed and the stock may move exactly once.
    import threading

    d = _cmd("এক কেজি পেঁয়াজ নগদ")
    before = [p for p in client.get("/api/products").json()
              if p["name_en"] == "onion"][0]["stock"]
    results = []

    def go():
        results.append(
            client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code
        )

    ts = [threading.Thread(target=go) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results) == [200, 400, 400, 400]
    after = [p for p in client.get("/api/products").json()
             if p["name_en"] == "onion"][0]["stock"]
    assert after == before - 1


def test_paid_void_refused_via_api():
    # FIX-PLAN-2 D3 at the API layer, on a fresh DB (own customer
    # history): both undo paths must refuse a paid due sale.
    old = main_mod._conn
    main_mod._conn = init_db(get_conn(":memory:"))
    try:
        d = _cmd("রহিমদার নামে দুই কেজি চিনি বাকিতে")
        assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 200
        d = _cmd("রহিমদা ১২০ টাকা জমা দিলেন")
        assert client.post(f"/api/drafts/{d['draft_id']}/confirm").status_code == 200
        d = _cmd("শেষ বিক্রিটা বাতিল করো")
        r = client.post(f"/api/drafts/{d['draft_id']}/confirm")
        assert r.status_code == 400 and "জমা" in r.json()["detail"]
        # Batch G4: there is no direct undo endpoint any more — undo
        # only ever happens through a confirmed draft. (405, not 404:
        # the static-frontend mount answers unknown POST paths.)
        assert client.post("/api/undo-last-sale").status_code in (404, 405)
        dues = client.get("/api/dues").json()
        assert dues == []  # due is 0, never -120
    finally:
        main_mod._conn = old


# ---------- Batch G regressions ----------

def _product(name_en):
    return [p for p in client.get("/api/products").json()
            if p["name_en"] == name_en][0]


def test_duplicate_lines_cannot_oversell():
    # G1 probe: the same product on two lines, each within stock, used
    # to confirm and drive the stock negative — the check must be on
    # the aggregated quantity.
    sugar = _product("sugar")
    stock = sugar["stock"]
    d = _cmd("এক কেজি চিনি নগদ")
    r = client.put(
        f"/api/drafts/{d['draft_id']}/items",
        json={"items": [
            {"product_id": sugar["id"], "qty": stock},
            {"product_id": sugar["id"], "qty": stock},
        ]},
    )
    assert r.status_code == 200
    r = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert r.status_code == 400
    assert _product("sugar")["stock"] == stock  # nothing was sold


def test_put_items_bad_input_is_422_not_500():
    sugar = _product("sugar")
    d = _cmd("এক কেজি চিনি নগদ")
    r = client.put(f"/api/drafts/{d['draft_id']}/items",
                   json={"items": [{"product_id": sugar["id"]}]})
    assert r.status_code == 422
    r = client.put(f"/api/drafts/{d['draft_id']}/items",
                   json={"items": [{"product_id": sugar["id"], "qty": "abc"}]})
    assert r.status_code == 422


def test_products_do_not_leak_cost():
    for p in client.get("/api/products").json():
        assert "cost" not in p


def test_confirm_rejects_unknown_payment_type():
    # G3 backstop: even a hand-crafted draft (or a misbehaving model)
    # cannot store a sale with a payment type the ledger ignores.
    sugar = _product("sugar")
    draft_id = main_mod._save_draft({
        "kind": "sale",
        "items": [{"product_id": sugar["id"], "name_bn": "চিনি", "qty": 1,
                   "unit": "kg", "unit_price": 60, "line_total": 60}],
        "customer": None, "payment_type": "credit", "total": 60,
        "ambiguities": [],
    })
    r = client.post(f"/api/drafts/{draft_id}/confirm")
    assert r.status_code == 400
    assert _product("sugar")["stock"] == sugar["stock"]


def test_summary_today_means_calendar_day():
    # G7: a sale from yesterday 23:59 must not appear in "আজ" — the
    # dashboard card now agrees with the voice answer.
    from datetime import date, timedelta

    before = client.get("/api/reports/summary?days=1").json()
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d 23:59:59")
    main_mod._conn.execute(
        "INSERT INTO sales (created_at, customer_id, payment_type, total, status)"
        " VALUES (?, NULL, 'cash', 777, 'confirmed')",
        (yesterday,),
    )
    main_mod._conn.commit()
    after = client.get("/api/reports/summary?days=1").json()
    assert after["sales_total"] == before["sales_total"]
    assert after["sales_count"] == before["sales_count"]
