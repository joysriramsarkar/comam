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
    # the shopkeeper reviews the item list (PUT) — that settles the warning
    items = [
        {"product_id": i["product_id"], "qty": i["qty"]}
        for i in d["draft"]["items"]
    ]
    r3 = client.put(f"/api/drafts/{d['draft_id']}/items", json={"items": items})
    assert r3.status_code == 200
    r4 = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert r4.status_code == 200
    sale = r4.json()["result"]["sale"]
    assert sale["payment_type"] == "upi" and sale["total"] == 31
    names = [c["name"] for c in client.get("/api/customers").json()]
    assert "জয় সরকার" in names
