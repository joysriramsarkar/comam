import sys
from pathlib import Path

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
