"""Seed 7 days of demo sales so reports/low-stock have something to say.
Run: python scripts/seed_demo.py  (creates backend/comin.db if needed)"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.db import get_conn, init_db          # noqa: E402
from app.ledger import Ledger               # noqa: E402

random.seed(42)
conn = init_db(get_conn())
led = Ledger(conn)
products = conn.execute("SELECT * FROM products").fetchall()
customers = conn.execute("SELECT * FROM customers").fetchall()

for days_ago in range(7, 0, -1):
    when = datetime.now() - timedelta(days=days_ago)
    for _ in range(random.randint(3, 6)):
        chosen = random.sample(products, random.randint(1, 3))
        items = [
            {"product_id": p["id"], "qty": random.choice([0.5, 1, 2, 3]) if p["unit"] == "kg"
             else random.choice([1, 2, 4])}
            for p in chosen
        ]
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
print("dues:", led.all_dues())
print("low stock:", [p["name_bn"] for p in led.low_stock()])
