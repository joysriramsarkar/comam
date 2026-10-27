"""Eval harness for the deterministic local parser (offline baseline).

Runs eval/commands.json through parser_local and scores:
kind accuracy, total accuracy (sale/purchase), ambiguity detection, and
per-item cart accuracy (cases that carry an "items" expectation — the
exact products and quantities, so a wrong cart with a right total fails).
The Spark path gets the same set later, once an API key is configured —
voice recordings (the ~100-command spoken set) plug into the same file.

Run: python scripts/run_eval.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.db import get_conn, init_db          # noqa: E402
from app.parser_local import parse_command    # noqa: E402

cases = json.loads((ROOT / "eval" / "commands.json").read_text(encoding="utf-8"))
conn = init_db(get_conn(":memory:"))

kind_ok = total_ok = total_n = amb_ok = amb_n = 0
item_ok = item_n = 0
failures = []
for case in cases:
    d = parse_command(conn, case["text"])
    ok = d["kind"] == case["kind"]
    if "report" in case:
        ok = ok and d.get("report") == case["report"]
    if "customer" in case:
        got_name = (d.get("customer") or {}).get("name") or d.get("customer_name")
        ok = ok and got_name == case["customer"]
    if "payment_type" in case:
        ok = ok and d.get("payment_type") == case["payment_type"]
    if "amount" in case:
        ok = ok and d.get("amount") == case["amount"]
    kind_ok += ok
    if "total" in case:
        total_n += 1
        tok = abs(d.get("total", -1) - case["total"]) < 0.01
        total_ok += tok
        ok = ok and tok
    if "ambiguous" in case:
        amb_n += 1
        aok = bool(d.get("ambiguities")) == case["ambiguous"]
        amb_ok += aok
        ok = ok and aok
    if "items" in case:
        item_n += 1
        got_items = sorted((i["name_bn"], round(float(i["qty"]), 3)) for i in d.get("items", []))
        exp_items = sorted((i["name_bn"], round(float(i["qty"]), 3)) for i in case["items"])
        iok = got_items == exp_items
        item_ok += iok
        ok = ok and iok
    if not ok:
        failures.append((case, d))

n = len(cases)
print(f"cases: {n}")
print(f"intent/customer accuracy: {kind_ok}/{n} = {kind_ok / n * 100:.1f}%")
print(f"total accuracy (sale/purchase): {total_ok}/{total_n} = {total_ok / total_n * 100:.1f}%")
if amb_n:
    print(f"ambiguity detection: {amb_ok}/{amb_n}")
if item_n:
    print(f"cart accuracy (exact items): {item_ok}/{item_n} = {item_ok / item_n * 100:.1f}%")
if failures:
    print("\nfailures:")
    for case, d in failures:
        got = {k: d.get(k) for k in ("kind", "total", "report", "payment_type")}
        print(f"  - {case['text']}\n    expected {case}\n    got {got} items={[(i['name_bn'], i['qty']) for i in d.get('items', [])]}")
sys.exit(1 if failures else 0)
