"""Business logic: sales, stock, due ledger, reports.

Every total is computed here from database prices — never taken from the
LLM or the client. Sales only happen through create_sale(), which the API
calls AFTER the shopkeeper confirms a draft.
"""
from datetime import datetime, timedelta


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Ledger:
    def __init__(self, conn):
        self.conn = conn

    # ---------- customers ----------
    def get_or_create_customer(self, name):
        row = self.conn.execute(
            "SELECT * FROM customers WHERE name=?", (name,)
        ).fetchone()
        if row is not None:
            return row
        cur = self.conn.execute(
            "INSERT INTO customers (name, aliases, phone) VALUES (?,?,?)",
            (name, "", ""),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM customers WHERE id=?", (cur.lastrowid,)
        ).fetchone()

    # ---------- sales ----------
    def create_sale(self, items, customer_id=None, payment_type="cash"):
        """items: [{product_id, qty}]. Prices come from the DB.
        Returns the sale dict with computed totals.

        The ledger guards itself (Batch G1): callers — API, seed, future
        integrations — cannot create a sale with qty <= 0, more than the
        stock (counted across duplicate lines for the same product), a
        due sale with no customer, or an unknown payment type. All checks
        run before any write, so a refused sale writes nothing."""
        if payment_type not in ("cash", "due", "upi"):
            raise ValueError(f"unknown payment_type {payment_type!r}")
        if payment_type == "due" and customer_id is None:
            raise ValueError("বাকি বিক্রির জন্য ক্রেতা দরকার")
        if not items:
            raise ValueError("কোনো পণ্য নেই — খালি বিল সেভ হবে না")
        rows = []
        total = 0.0
        wanted = {}
        products = {}
        for it in items:
            p = self.conn.execute(
                "SELECT * FROM products WHERE id=?", (it["product_id"],)
            ).fetchone()
            if p is None:
                raise ValueError(f"unknown product {it['product_id']}")
            qty = float(it["qty"])
            if qty <= 0:
                raise ValueError("পরিমাণ শূন্যের বেশি হতে হবে")
            wanted[p["id"]] = wanted.get(p["id"], 0.0) + qty
            products[p["id"]] = p
            line = round(p["price"] * qty, 2)
            total += line
            rows.append((p, qty, line))
        for pid, qty in wanted.items():
            p = products[pid]
            if qty > float(p["stock"]):
                raise ValueError(
                    f"স্টকে {p['name_bn']} আছে {p['stock']} {p['unit']}"
                    " — এর বেশি বিক্রি হবে না"
                )
        total = round(total, 2)
        cur = self.conn.execute(
            "INSERT INTO sales (created_at, customer_id, payment_type, total) VALUES (?,?,?,?)",
            (_now(), customer_id, payment_type, total),
        )
        sale_id = cur.lastrowid
        for p, qty, line in rows:
            self.conn.execute(
                "INSERT INTO sale_items (sale_id, product_id, qty, unit_price, unit_cost, line_total)"
                " VALUES (?,?,?,?,?,?)",
                # unit_cost is a snapshot: later purchase-price changes
                # must not rewrite this sale's profit (FIX-PLAN-2 E1).
                (sale_id, p["id"], qty, p["price"], p["cost"], line),
            )
            self.conn.execute(
                "UPDATE products SET stock = stock - ? WHERE id=?", (qty, p["id"])
            )
        self._audit("sale_created", f"sale {sale_id} total {total} {payment_type}")
        self.conn.commit()
        return self.get_sale(sale_id)

    def get_sale(self, sale_id):
        s = self.conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if s is None:
            return None
        items = self.conn.execute(
            "SELECT si.*, p.name_bn, p.unit FROM sale_items si"
            " JOIN products p ON p.id = si.product_id WHERE si.sale_id=?",
            (sale_id,),
        ).fetchall()
        d = dict(s)
        d["items"] = [dict(i) for i in items]
        return d

    def last_sale(self):
        s = self.conn.execute(
            "SELECT * FROM sales WHERE status='confirmed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(s) if s is not None else None

    def void_sale(self, sale_id):
        """Void one specific confirmed sale and restore its stock.
        Returns the sale dict, or None if it is not voidable.
        Raises ValueError when voiding a due sale whose payment has
        already been taken would push the customer's due negative
        (FIX-PLAN-2 D3) — the guard lives here so every undo path
        (draft confirm, /api/undo-last-sale) is held to it."""
        s = self.conn.execute(
            "SELECT * FROM sales WHERE id=? AND status='confirmed'", (sale_id,)
        ).fetchone()
        if s is None:
            return None
        if s["payment_type"] == "due" and s["customer_id"] is not None:
            due_sales = self.conn.execute(
                "SELECT COALESCE(SUM(total),0) t FROM sales"
                " WHERE customer_id=? AND payment_type='due' AND status='confirmed'",
                (s["customer_id"],),
            ).fetchone()["t"]
            paid = self.conn.execute(
                "SELECT COALESCE(SUM(amount),0) t FROM due_payments WHERE customer_id=?",
                (s["customer_id"],),
            ).fetchone()["t"]
            if paid > due_sales - s["total"] + 1e-9:
                raise ValueError(
                    "এই বিক্রির বিপরীতে টাকা জমা পড়ে গেছে — বাতিল করলে "
                    "বাকির হিসাব ঋণাত্মক হয়ে যাবে। আগে জমাটা ফেরত বা "
                    "সমন্বয় করুন।"
                )
        for it in self.conn.execute(
            "SELECT * FROM sale_items WHERE sale_id=?", (s["id"],)
        ).fetchall():
            self.conn.execute(
                "UPDATE products SET stock = stock + ? WHERE id=?",
                (it["qty"], it["product_id"]),
            )
        self.conn.execute("UPDATE sales SET status='void' WHERE id=?", (s["id"],))
        self._audit("sale_voided", f"sale {s['id']} total {s['total']} undone")
        self.conn.commit()
        return dict(s)

    def void_last_sale(self):
        s = self.last_sale()
        if s is None:
            return None
        return self.void_sale(s["id"])

    # ---------- due ledger (বাকি খাতা) ----------
    def record_due_payment(self, customer_id, amount, note=""):
        self.conn.execute(
            "INSERT INTO due_payments (customer_id, amount, created_at, note) VALUES (?,?,?,?)",
            (customer_id, float(amount), _now(), note),
        )
        self._audit("due_payment", f"customer {customer_id} paid {amount}")
        self.conn.commit()
        return self.customer_due(customer_id)

    def customer_due(self, customer_id):
        due_sales = self.conn.execute(
            "SELECT COALESCE(SUM(total),0) s FROM sales"
            " WHERE customer_id=? AND payment_type='due' AND status='confirmed'",
            (customer_id,),
        ).fetchone()["s"]
        paid = self.conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM due_payments WHERE customer_id=?",
            (customer_id,),
        ).fetchone()["s"]
        return round(due_sales - paid, 2)

    def all_dues(self):
        out = []
        for c in self.conn.execute("SELECT * FROM customers").fetchall():
            due = self.customer_due(c["id"])
            if due > 0:
                out.append({"customer_id": c["id"], "customer": c["name"], "due": due})
        return out

    # ---------- purchases / stock in ----------
    def record_purchases(self, items):
        """Stock in a whole purchase in ONE transaction (FIX-PLAN-2 D2).
        items: [{product_id, qty, cost_per_unit?}]. Every product is
        validated before anything is written — one bad id means the
        purchase writes nothing, never half of it."""
        rows = []
        for it in items:
            p = self.conn.execute(
                "SELECT id FROM products WHERE id=?", (it["product_id"],)
            ).fetchone()
            if p is None:
                raise ValueError(f"unknown product {it['product_id']}")
            if float(it["qty"]) <= 0:
                raise ValueError("পরিমাণ শূন্যের বেশি হতে হবে")
            rows.append(it)
        for it in rows:
            self.conn.execute(
                "UPDATE products SET stock = stock + ? WHERE id=?",
                (float(it["qty"]), it["product_id"]),
            )
            if it.get("cost_per_unit") is not None:
                self.conn.execute(
                    "UPDATE products SET cost=? WHERE id=?",
                    (float(it["cost_per_unit"]), it["product_id"]),
                )
            self._audit("purchase", f"product {it['product_id']} +{it['qty']}")
        self.conn.commit()

    def record_purchase(self, product_id, qty, cost_per_unit=None):
        item = {"product_id": product_id, "qty": qty}
        if cost_per_unit is not None:
            item["cost_per_unit"] = cost_per_unit
        self.record_purchases([item])

    # ---------- reports ----------
    def sales_summary(self, days=1, calendar=False):
        if calendar and days == 1:
            # "আজ" means the calendar day, so it agrees with daily_sales()
            # and the chart — not a rolling 24-hour window.
            since = datetime.now().strftime("%Y-%m-%d 00:00:00")
        else:
            since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        row = self.conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM sales"
            " WHERE status='confirmed' AND created_at>=?",
            (since,),
        ).fetchone()
        profit = self.conn.execute(
            # unit_cost snapshot from sale time — NOT the product's
            # current cost, which a newer purchase may have changed.
            "SELECT COALESCE(SUM(si.line_total - si.unit_cost*si.qty),0) pr"
            " FROM sale_items si"
            " JOIN sales s ON s.id=si.sale_id"
            " WHERE s.status='confirmed' AND s.created_at>=?",
            (since,),
        ).fetchone()["pr"]
        return {
            "days": days,
            "sales_count": row["n"],
            "sales_total": round(row["s"], 2),
            "est_profit": round(profit, 2),
        }

    def daily_sales(self, days=7):
        out = []
        for back in range(days - 1, -1, -1):
            day = datetime.now() - timedelta(days=back)
            start = day.strftime("%Y-%m-%d 00:00:00")
            end = day.strftime("%Y-%m-%d 23:59:59")
            row = self.conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(total),0) s FROM sales"
                " WHERE status='confirmed' AND created_at>=? AND created_at<=?",
                (start, end),
            ).fetchone()
            out.append(
                {"date": day.strftime("%Y-%m-%d"), "count": row["n"], "total": round(row["s"], 2)}
            )
        return out

    def week_compare(self):
        cur = self.sales_summary(7)["sales_total"]
        since = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        until = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        prev = self.conn.execute(
            "SELECT COALESCE(SUM(total),0) s FROM sales WHERE status='confirmed'"
            " AND created_at>=? AND created_at<?",
            (since, until),
        ).fetchone()["s"]
        change = None
        if prev:
            change = round((cur - prev) / prev * 100, 1)
        return {"this_week": cur, "last_week": round(prev, 2), "change_pct": change}

    def top_products(self, days=7, limit=5):
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = self.conn.execute(
            "SELECT p.name_bn, SUM(si.qty) qty, SUM(si.line_total) amount"
            " FROM sale_items si JOIN products p ON p.id=si.product_id"
            " JOIN sales s ON s.id=si.sale_id"
            " WHERE s.status='confirmed' AND s.created_at>=?"
            " GROUP BY p.id ORDER BY amount DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def low_stock(self):
        rows = self.conn.execute(
            "SELECT * FROM products WHERE stock <= low_stock_threshold ORDER BY stock"
        ).fetchall()
        return [dict(r) for r in rows]

    def _audit(self, action, detail):
        self.conn.execute(
            "INSERT INTO audit_log (created_at, action, detail) VALUES (?,?,?)",
            (_now(), action, detail),
        )
