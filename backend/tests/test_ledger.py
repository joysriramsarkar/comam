import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import get_conn, init_db       # noqa: E402
from app.ledger import Ledger            # noqa: E402


def make():
    conn = init_db(get_conn(":memory:"))
    return conn, Ledger(conn)


def test_sale_total_from_db_prices_and_stock():
    conn, led = make()
    chini = conn.execute("SELECT * FROM products WHERE name_en='sugar'").fetchone()
    before = chini["stock"]
    sale = led.create_sale([{"product_id": chini["id"], "qty": 2}])
    assert sale["total"] == 120  # 2 kg x 60, price from DB
    after = conn.execute("SELECT stock FROM products WHERE id=?", (chini["id"],)).fetchone()
    assert after["stock"] == before - 2


def test_due_flow():
    conn, led = make()
    saban = conn.execute("SELECT * FROM products WHERE name_en='soap'").fetchone()
    led.create_sale([{"product_id": saban["id"], "qty": 3}], customer_id=1, payment_type="due")
    assert led.customer_due(1) == 105
    led.record_due_payment(1, 100)
    assert led.customer_due(1) == 5


def test_void_restores_stock():
    conn, led = make()
    dim = conn.execute("SELECT * FROM products WHERE name_en='egg'").fetchone()
    stock0 = dim["stock"]
    led.create_sale([{"product_id": dim["id"], "qty": 10}])
    led.void_last_sale()
    stock1 = conn.execute("SELECT stock FROM products WHERE id=?", (dim["id"],)).fetchone()
    assert stock1["stock"] == stock0


def test_purchase_batch_is_atomic():
    conn, led = make()
    chini = conn.execute("SELECT * FROM products WHERE name_en='sugar'").fetchone()
    before = chini["stock"]
    try:
        led.record_purchases([{"product_id": chini["id"], "qty": 5},
                              {"product_id": 99999, "qty": 1}])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    after = conn.execute(
        "SELECT stock FROM products WHERE id=?", (chini["id"],)
    ).fetchone()
    assert after["stock"] == before  # nothing half-written
    led.record_purchases([{"product_id": chini["id"], "qty": 5}])
    after = conn.execute(
        "SELECT stock FROM products WHERE id=?", (chini["id"],)
    ).fetchone()
    assert after["stock"] == before + 5


def test_paid_due_sale_void_refused():
    # FIX-PLAN-2 D3: voiding a due sale whose money was already taken
    # would push the customer's due negative — refuse, in the ledger.
    conn, led = make()
    chini = conn.execute("SELECT * FROM products WHERE name_en='sugar'").fetchone()
    sale = led.create_sale([{"product_id": chini["id"], "qty": 2}],
                           customer_id=1, payment_type="due")
    assert led.customer_due(1) == 120
    led.record_due_payment(1, 120)
    assert led.customer_due(1) == 0
    try:
        led.void_sale(sale["id"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert led.customer_due(1) == 0
    stock = conn.execute(
        "SELECT stock FROM products WHERE id=?", (chini["id"],)
    ).fetchone()["stock"]
    assert stock == chini["stock"] - 2  # stock NOT restored on refusal
    # a partial payment refuses too (due would go to -50)
    sale2 = led.create_sale([{"product_id": chini["id"], "qty": 2}],
                            customer_id=2, payment_type="due")
    led.record_due_payment(2, 50)
    try:
        led.void_sale(sale2["id"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    # an unpaid due sale still voids cleanly
    sale3 = led.create_sale([{"product_id": chini["id"], "qty": 1}],
                            customer_id=3, payment_type="due")
    assert led.void_sale(sale3["id"]) is not None
    assert led.customer_due(3) == 0


def test_profit_uses_sale_time_cost_snapshot():
    # FIX-PLAN-2 E1: raising the purchase price later must not
    # rewrite the profit of sales already made.
    conn, led = make()
    chini = conn.execute("SELECT * FROM products WHERE name_en='sugar'").fetchone()
    led.create_sale([{"product_id": chini["id"], "qty": 1}])  # cost 52, price 60
    assert led.sales_summary(1, calendar=True)["est_profit"] == 8
    conn.execute("UPDATE products SET cost=99 WHERE id=?", (chini["id"],))
    conn.commit()
    assert led.sales_summary(1, calendar=True)["est_profit"] == 8
    led.create_sale([{"product_id": chini["id"], "qty": 1}])  # now cost 99
    assert led.sales_summary(1, calendar=True)["est_profit"] == 8 + (60 - 99)


def test_unit_cost_migration_on_old_db():
    # A DB created before unit_cost existed gains the column in init_db.
    import sqlite3

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute(
        "CREATE TABLE sale_items (id INTEGER PRIMARY KEY,"
        " sale_id INTEGER NOT NULL, product_id INTEGER NOT NULL,"
        " qty REAL NOT NULL, unit_price REAL NOT NULL, line_total REAL NOT NULL)"
    )
    init_db(raw)
    cols = [r["name"] for r in raw.execute("PRAGMA table_info(sale_items)")]
    assert "unit_cost" in cols


# ---------- Batch G1: the ledger guards itself ----------

def test_ledger_rejects_duplicate_line_oversell():
    conn, led = make()
    chini = conn.execute("SELECT * FROM products WHERE name_en='sugar'").fetchone()
    stock = chini["stock"]
    half = stock / 2 + 1  # each line within stock, the total is not
    with pytest.raises(ValueError):
        led.create_sale([
            {"product_id": chini["id"], "qty": half},
            {"product_id": chini["id"], "qty": half},
        ])
    after = conn.execute("SELECT stock FROM products WHERE id=?",
                         (chini["id"],)).fetchone()
    assert after["stock"] == stock  # refused sale wrote nothing


def test_ledger_rejects_bad_sales():
    conn, led = make()
    chini = conn.execute("SELECT * FROM products WHERE name_en='sugar'").fetchone()
    with pytest.raises(ValueError):
        led.create_sale([{"product_id": chini["id"], "qty": -2}])
    with pytest.raises(ValueError):
        led.create_sale([{"product_id": chini["id"], "qty": 1}],
                        payment_type="credit")
    with pytest.raises(ValueError):
        led.create_sale([{"product_id": chini["id"], "qty": 1}],
                        payment_type="due")  # no customer
    with pytest.raises(ValueError):
        led.create_sale([])


def test_ledger_rejects_nonpositive_purchase():
    conn, led = make()
    chini = conn.execute("SELECT * FROM products WHERE name_en='sugar'").fetchone()
    with pytest.raises(ValueError):
        led.record_purchases([{"product_id": chini["id"], "qty": 0}])
