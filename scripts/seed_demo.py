"""Seed 14 days of demo sales so reports/low-stock have something to say
— including a previous week, so week-vs-week comparison has a baseline.

Idempotent: existing sales/payments are cleared and product stock is
restored to the seed values first, so re-running gives the same demo.

Run: python scripts/seed_demo.py  (creates backend/comin.db if needed)"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.db import SEED_PRODUCTS, get_conn, init_db   # noqa: E402
from app.ledger import Ledger                          # noqa: E402

random.seed(42)
conn = init_db(get_conn())
led = Ledger(conn)

# --- reset to a clean demo state -------------------------------------
conn.execute("DELETE FROM sale_items")
conn.execute("DELETE FROM sales")
conn.execute("DELETE FROM due_payments")
for row in SEED_PRODUCTS:
    # tuple: name_bn, name_en, aliases, unit, price, cost, stock, threshold
    conn.execute("UPDATE products SET stock=? WHERE name_en=?", (row[6], row[1]))
conn.commit()

products = conn.execute("SELECT * FROM products").fetchall()
customers = conn.execute("SELECT * FROM customers").fetchall()

for days_ago in range(14, 0, -1):
    when = datetime.now() - timedelta(days=days_ago)
    for _ in range(random.randint(3, 6)):
        chosen = random.sample(products, random.randint(1, 3))
        items = []
        for p in chosen:
            qty = random.choice([0.5, 1, 2, 3]) if p["unit"] == "kg" \
                else random.choice([1, 2, 4])
            # The ledger refuses overselling (Batch G1), so the demo
            # generator caps each line at the stock actually on hand
            # and drops items that have run out.
            stock = conn.execute(
                "SELECT stock FROM products WHERE id=?", (p["id"],)
            ).fetchone()["stock"]
            qty = min(qty, stock)
            if qty > 0:
                items.append({"product_id": p["id"], "qty": qty})
        if not items:
            continue
        due = random.random() < 0.3
        sale = led.create_sale(
            items,
            customer_id=random.choice(customers)["id"] if due else None,
            payment_type="due" if due else "cash",
        )
        conn.execute("UPDATE sales SET created_at=? WHERE id=?",
                     (when.strftime("%Y-%m-%d %H:%M:%S"), sale["id"]))
    conn.commit()
# a couple of due payments
for c in customers[:2]:
    if led.customer_due(c["id"]) > 100:
        led.record_due_payment(c["id"], 100)
print("seeded. summary(7d):", led.sales_summary(7))
print("week compare:", led.week_compare())
print("dues:", led.all_dues())
print("low stock:", [p["name_bn"] for p in led.low_stock()])
