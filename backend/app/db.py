"""SQLite storage for comam AI.

All money and stock arithmetic happens in ledger.py on top of this schema.
The LLM never touches this layer directly.
"""
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "comin.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name_bn TEXT NOT NULL,
    name_en TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL,               -- kg | litre | piece
    price REAL NOT NULL,              -- selling price per unit (rupees)
    cost REAL NOT NULL DEFAULT 0,     -- purchase price per unit
    stock REAL NOT NULL DEFAULT 0,
    low_stock_threshold REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    customer_id INTEGER REFERENCES customers(id),
    payment_type TEXT NOT NULL DEFAULT 'cash',   -- cash | due
    total REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed'     -- confirmed | void
);
CREATE TABLE IF NOT EXISTS sale_items (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty REAL NOT NULL,
    unit_price REAL NOT NULL,
    unit_cost REAL NOT NULL DEFAULT 0,   -- snapshot of products.cost at sale time
    line_total REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS due_payments (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    kind TEXT NOT NULL,               -- sale | due_payment | undo
    payload TEXT NOT NULL,            -- JSON
    status TEXT NOT NULL DEFAULT 'pending'  -- pending | confirmed | rejected
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""

# name_bn, name_en, aliases (comma separated, incl. Banglish), unit, price, cost, stock, low threshold
SEED_PRODUCTS = [
    ("চিনি", "sugar", "chini,sugar,চিনি", "kg", 60, 52, 40, 10),
    ("মসুর ডাল", "red lentil", "dal,masur dal,মসুর,ডাল", "kg", 120, 104, 25, 8),
    ("সাবান", "soap", "saban,soap,সাবান", "piece", 35, 28, 60, 15),
    ("চাল", "rice", "chal,rice,চাল", "kg", 55, 47, 80, 20),
    ("আলু", "potato", "alu,potato,আলু", "kg", 30, 24, 60, 15),
    ("টমেটো", "tomato", "tomato,টমেটো", "kg", 40, 31, 20, 8),
    ("পেঁয়াজ", "onion", "peyaj,onion,পেঁয়াজ,পেয়াজ", "kg", 35, 27, 30, 10),
    ("সরিষার তেল", "mustard oil", "tel,oil,তেল,সরিষার তেল", "litre", 180, 158, 15, 5),
    ("ডিটারজেন্ট", "detergent", "detergent,ডিটারজেন্ট", "piece", 99, 82, 24, 6),
    ("বিস্কুট বড় প্যাকেট", "biscuit big pack", "biscuit,বিস্কুট", "piece", 20, 16, 40, 10),
    ("বিস্কুট ছোট প্যাকেট", "biscuit small pack", "biscuit,বিস্কুট", "piece", 10, 8, 50, 10),
    ("ডিম", "egg", "dim,egg,ডিম", "piece", 7, 6, 120, 30),
    ("আটা", "flour", "ata,flour,আটা", "kg", 45, 38, 35, 10),
    ("লবণ", "salt", "lobon,salt,লবণ,নুন", "kg", 25, 19, 20, 5),
    ("চা পাতা", "tea leaves", "cha,tea,চা,চা পাতা", "piece", 140, 118, 12, 4),
    ("দুধ", "milk", "dudh,milk,দুধ", "piece", 30, 26, 24, 8),
]

SEED_CUSTOMERS = [
    ("রহিম", "রহিমদা,rahim", ""),
    ("সুমিতা", "sumita,সুমিতা", ""),
    ("করিম", "করিমদা,karim", ""),
]


def get_conn(db_path=None):
    # check_same_thread=False: FastAPI serves requests from worker threads.
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    # Migration for DBs created before unit_cost existed (FIX-PLAN-2 E1):
    # CREATE TABLE IF NOT EXISTS never alters an existing table.
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(sale_items)")]
    if "unit_cost" not in cols:
        conn.execute(
            "ALTER TABLE sale_items ADD COLUMN unit_cost REAL NOT NULL DEFAULT 0"
        )
    if conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO products (name_bn,name_en,aliases,unit,price,cost,stock,low_stock_threshold)"
            " VALUES (?,?,?,?,?,?,?,?)",
            SEED_PRODUCTS,
        )
    if conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO customers (name,aliases,phone) VALUES (?,?,?)", SEED_CUSTOMERS
        )
    conn.commit()
    return conn
