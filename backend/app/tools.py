"""Whitelisted tools for Muse Spark function calling.

The LLM may ONLY act through these functions. They build a draft on a
DraftSession — they never commit to the database. The API layer commits
after the shopkeeper confirms. There is deliberately no generic
'run SQL' or 'write' tool.
"""
import json

from .catalog import quantity_warnings, search_products
from .ledger import Ledger

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_product",
            "description": "Search the shop catalog for a product by Bengali name, English name or Banglish spelling.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": "Add an item to the current draft bill. qty is in the product's own unit (kg, litre or piece).",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "qty": {"type": "number"},
                },
                "required": ["product_id", "qty"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_customer",
            "description": "Attach a known customer to the draft bill (needed for due/বাকি sales).",
            "parameters": {
                "type": "object",
                "properties": {"customer_id": {"type": "integer"}},
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_payment_type",
            "description": "Set how the bill is paid: cash (নগদ), due/credit (বাকি), or UPI (ইউপিআই).",
            "parameters": {
                "type": "object",
                "properties": {
                    "payment_type": {"type": "string", "enum": ["cash", "due", "upi"]}
                },
                "required": ["payment_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_customer_name",
            "description": "Name a customer who is NOT in the known-customer list. The customer is created when the draft is confirmed.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_due_payment",
            "description": "Draft a due-ledger payment: a customer paid back some of their বাকি. Give customer_id for a known customer, or customer_name for someone new (they are created when the draft is confirmed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer"},
                    "customer_name": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sales_summary",
            "description": "Sales total, count and estimated profit for the last N days.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer"}},
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_low_stock",
            "description": "Products at or below their low-stock threshold.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_products",
            "description": "Best-selling products by amount over the last N days.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer"}},
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_due",
            "description": "How much a customer currently owes (বাকি).",
            "parameters": {
                "type": "object",
                "properties": {"customer_id": {"type": "integer"}},
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_purchase",
            "description": "Mark this draft as a PURCHASE (কিনলাম — stock coming IN from a supplier), not a sale. Call this before/instead of treating the items as a sale whenever the shopkeeper says they bought stock.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_undo",
            "description": "The shopkeeper wants to undo/cancel the last sale (শেষ বিক্রিটা বাতিল করো). Builds an undo draft; nothing is cancelled until they confirm it.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_week_compare",
            "description": "Compare sales of the last 7 days with the 7 days before that.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_sales",
            "description": "Sales quantity and stock for a specific product today or over N days (e.g. 'আজকে আমুল শক্তি কয়টা বিক্রি হয়েছে?').",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "days": {"type": "integer"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_customer",
            "description": "Add a new customer to the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
]


class DraftSession:
    """Mutable draft the tools build up. Converted to the standard draft
    dict (same shape as parser_local) by to_draft()."""

    def __init__(self, conn):
        self.conn = conn
        self.items = []
        self.customer = None
        self.customer_name = None
        self.payment_type = "cash"
        self.due_payment = None
        self.ambiguities = []
        self.unknown_items = []
        self._amb_options = {}  # product_id -> options of an ambiguous search
        # Batch G2: intent markers so Spark drafts reach parity with the
        # local parser's kinds — purchase and undo used to collapse into
        # "sale"/"unknown", and reports came back as free-text replies
        # with LLM-narrated numbers.
        self.intent = "sale"        # "sale" | "purchase" (mark_purchase)
        self.undo = False           # request_undo
        self.report = None          # report tool name, e.g. "sales_summary"
        self.target_product = None  # target product for product_sales report
        self.due_query_customer = None  # get_customer_due target

    def to_draft(self, raw=""):
        if self.due_payment:
            draft = {
                "kind": "due_payment",
                "customer": self.customer,
                "amount": self.due_payment["amount"],
                "ambiguities": [],
                "raw": raw,
                "engine": "spark",
            }
            if self.customer_name and not self.customer:
                draft["customer_name"] = self.customer_name
            return draft
        if self.undo:
            return {"kind": "undo", "ambiguities": [], "raw": raw,
                    "engine": "spark"}
        total = round(sum(i["line_total"] for i in self.items), 2)
        draft = {
            "kind": (self.intent if self.items else "unknown"),
            "items": self.items,
            "customer": self.customer,
            "payment_type": self.payment_type,
            "total": total,
            "ambiguities": self.ambiguities,
            "raw": raw,
            "engine": "spark",
        }
        if not self.items and self.report:
            # The API answers report drafts itself, from the ledger, in
            # deterministic code — the model's narration never reaches
            # the shopkeeper (Batch G2).
            rep_draft = {"kind": "report", "report": self.report,
                         "ambiguities": [], "raw": raw, "engine": "spark"}
            if self.target_product is not None:
                rep_draft["target_product"] = self.target_product
            return rep_draft
        if not self.items and self.due_query_customer is not None:
            return {"kind": "due_query", "customer": self.due_query_customer,
                    "ambiguities": [], "raw": raw, "engine": "spark"}
        if self.unknown_items:
            # Same shape as the local parser: confirm stays blocked until
            # the shopkeeper reviews the draft, so nothing is dropped.
            draft["unknown_items"] = self.unknown_items
        warns = quantity_warnings(self.items)
        if warns:
            # Advisory only (ASR পৌনে→পনেরো mitigation), same as parser.
            draft["warnings"] = warns
        if self.customer_name and not self.customer:
            draft["customer_name"] = self.customer_name
            draft["customer"] = {"id": None, "name": self.customer_name}
        return draft


def dispatch(session: DraftSession, name: str, args: dict):
    """Execute one whitelisted tool call against the draft session / DB
    (read-only queries hit the DB; writes only touch the draft)."""
    conn = session.conn
    led = Ledger(conn)
    if name == "search_product":
        matches, ambiguous = search_products(conn, args["query"])
        if not matches:
            # Never let a spoken item vanish: it lands on the draft as an
            # unknown item, exactly like the local parser's drafts.
            if not any(u["text"] == args["query"] for u in session.unknown_items):
                session.unknown_items.append(
                    {"text": args["query"], "qty": 1.0, "stated_price": None}
                )
        if ambiguous:
            options = [
                {"product_id": p["id"], "name_bn": p["name_bn"], "price": p["price"]}
                for p, _ in matches[:3]
            ]
            if not any(a["text"] == args["query"] for a in session.ambiguities):
                session.ambiguities.append(
                    {"text": args["query"], "options": options,
                     "product_id": matches[0][0]["id"]}
                )
            for o in options:
                session._amb_options[o["product_id"]] = options
        return {
            "matches": [
                {
                    "product_id": p["id"],
                    "name_bn": p["name_bn"],
                    "unit": p["unit"],
                    "price": p["price"],
                    "score": round(s, 3),
                }
                for p, s in matches
            ],
            "ambiguous": ambiguous,
        }
    if name == "add_to_cart":
        p = conn.execute(
            "SELECT * FROM products WHERE id=?", (args["product_id"],)
        ).fetchone()
        if p is None:
            return {"error": "unknown product"}
        qty = float(args["qty"])
        if qty <= 0:
            return {"error": "qty must be positive"}
        item = {
            "product_id": p["id"],
            "name_bn": p["name_bn"],
            "qty": qty,
            "unit": p["unit"],
            "unit_price": p["price"],
            "line_total": round(p["price"] * qty, 2),
        }
        if p["id"] in session._amb_options:
            # Picked from an ambiguous search: flag it so confirm stays
            # blocked until the shopkeeper resolves it, as locally.
            item["ambiguous"] = True
            item["options"] = session._amb_options[p["id"]]
        session.items.append(item)
        return {"added": p["name_bn"], "qty": qty}
    if name == "set_customer":
        c = conn.execute(
            "SELECT * FROM customers WHERE id=?", (args["customer_id"],)
        ).fetchone()
        if c is None:
            return {"error": "unknown customer"}
        session.customer = {"id": c["id"], "name": c["name"]}
        return {"customer": c["name"]}
    if name == "set_payment_type":
        # Validate HERE, not just in the schema enum (Batch G3): an
        # out-of-enum value like "credit" used to be stored on the sale
        # while the due ledger only counts "due" — the money vanished
        # from every total. Confirm validates again as a backstop.
        if args["payment_type"] not in ("cash", "due", "upi"):
            return {"error": "payment_type must be one of: cash, due, upi"}
        session.payment_type = args["payment_type"]
        return {"payment_type": session.payment_type}
    if name == "mark_purchase":
        session.intent = "purchase"
        return {"intent": "purchase"}
    if name == "request_undo":
        session.undo = True
        return {"undo_draft": True}
    if name == "set_customer_name":
        session.customer_name = str(args["name"]).strip()
        return {"customer_name": session.customer_name, "new_customer": True}
    if name == "record_due_payment":
        if float(args["amount"]) <= 0:
            return {"error": "amount must be positive"}
        if args.get("customer_id") is not None:
            c = conn.execute(
                "SELECT * FROM customers WHERE id=?", (args["customer_id"],)
            ).fetchone()
            if c is None:
                return {"error": "unknown customer"}
            session.customer = {"id": c["id"], "name": c["name"]}
            session.due_payment = {"amount": float(args["amount"])}
            return {"drafted_payment_for": c["name"], "amount": args["amount"]}
        if args.get("customer_name"):
            # New-customer payment (Batch G2 parity with the local
            # parser): the draft carries the name; confirm creates the
            # customer, and the usual due cap still applies there.
            session.customer_name = str(args["customer_name"]).strip()
            session.due_payment = {"amount": float(args["amount"])}
            return {"drafted_payment_for": session.customer_name,
                    "amount": args["amount"], "new_customer": True}
        return {"error": "customer_id or customer_name required"}
    if name == "get_sales_summary":
        session.report = "sales_summary"
        return led.sales_summary(int(args.get("days", 1)))
    if name == "get_low_stock":
        session.report = "low_stock"
        return {"low_stock": led.low_stock()}
    if name == "top_products":
        session.report = "top_products"
        return {"top": led.top_products(int(args.get("days", 7)))}
    if name == "get_week_compare":
        session.report = "week_compare"
        return led.week_compare()
    if name == "get_customer_due":
        c = conn.execute(
            "SELECT id, name FROM customers WHERE id=?",
            (int(args["customer_id"]),),
        ).fetchone()
        if c is None:
            return {"error": "unknown customer"}
        session.due_query_customer = {"id": c["id"], "name": c["name"]}
        return {"due": led.customer_due(c["id"])}
    if name == "get_product_sales":
        session.report = "product_sales"
        p = conn.execute("SELECT id, name_bn, unit, stock FROM products WHERE id=?", (args["product_id"],)).fetchone()
        if p:
            session.target_product = {"id": p["id"], "name_bn": p["name_bn"], "unit": p["unit"]}
        return led.product_sales(args["product_id"], int(args.get("days", 1)))
    if name == "create_customer":
        c = led.create_customer(args["name"], args.get("phone", ""))
        session.customer = {"id": c["id"], "name": c["name"]}
        return {"customer_id": c["id"], "name": c["name"]}
    return {"error": f"tool not whitelisted: {name}"}


def customers_brief(conn):
    return [
        {"id": c["id"], "name": c["name"]}
        for c in conn.execute("SELECT id, name FROM customers").fetchall()
    ]


SYSTEM_PROMPT = """You are the language layer of comam AI, a Bengali shop assistant.
The shopkeeper speaks Bengali (sometimes Banglish). Your ONLY way to act is the
provided tools. Rules:
- For a sale: search_product for each item, add_to_cart with the exact
  quantity in the product's unit (convert gram->kg by dividing by 1000,
  e.g. 500 gram of a kg product is qty 0.5). If a search is ambiguous
  (ambiguous=true), do NOT pick one: report the options back instead.
- If a customer name is mentioned and it matches a known customer, call
  set_customer. If the name is NOT a known customer, call set_customer_name
  with the name as spoken (do not drop it). If they say বাকি/নামে,
  set_payment_type due; নগদ means cash; ইউপিআই/UPI means upi.
- If search_product finds no match for an item, do NOT invent a price or
  skip it silently: tell the shopkeeper that item is not in the catalog.
- A price spoken before an item ('দশ টাকার বিস্কুট') states which priced
  variant they mean, not a quantity.
- For a due payment (জমা দিলেন), call record_due_payment.
- If the shopkeeper BOUGHT stock (কিনলাম/কিনেছি — goods coming IN), call
  mark_purchase, then search_product/add_to_cart for the items as usual.
  Without mark_purchase the draft is a sale and stock goes the wrong way.
- If they ask to cancel the last sale (বাতিল করো), call request_undo and
  nothing else — the system builds an undo draft for them to confirm.
- For business questions (sales, low stock, top products, week compare,
  a customer's বাকি), call the matching report/due tool and keep your
  own reply to one short line: the system answers with the real numbers
  computed in code, so never state amounts yourself.
- Never invent prices or totals: prices come from search_product results,
  totals are computed by the system after you finish.
Known customers: {customers}
Reply in Bengali, briefly."""
