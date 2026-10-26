import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import get_conn, init_db            # noqa: E402
from app.parser_local import parse_command     # noqa: E402


def make():
    return init_db(get_conn(":memory:"))


def test_full_bill_with_customer_and_due():
    conn = make()
    d = parse_command(conn, "দুই কেজি চিনি, আধা কেজি মসুর ডাল, তিনটে সাবান, রহিমদার নামে বাকিতে")
    assert d["kind"] == "sale"
    assert d["payment_type"] == "due"
    assert d["customer"]["name"] == "রহিম"
    assert len(d["items"]) == 3
    assert d["total"] == 285  # 120 + 60 + 105


def test_grams_convert_to_kg():
    conn = make()
    d = parse_command(conn, "৫০০ গ্রাম মসুর ডাল")
    assert d["items"][0]["qty"] == 0.5


def test_spoken_fraction():
    conn = make()
    d = parse_command(conn, "সাড়ে তিন কেজি আলু নগদ")
    assert d["items"][0]["qty"] == 3.5
    assert d["payment_type"] == "cash"


def test_biscuit_ambiguity_flagged_not_guessed():
    conn = make()
    d = parse_command(conn, "দুইটা বিস্কুট দাও")
    assert d["ambiguities"], "biscuit has two pack sizes — must ask, not guess"
    assert len(d["ambiguities"][0]["options"]) == 2


def test_due_payment():
    conn = make()
    d = parse_command(conn, "রহিমদা ২০০ টাকা জমা দিলেন")
    assert d["kind"] == "due_payment"
    assert d["amount"] == 200


def test_due_query_and_report():
    conn = make()
    assert parse_command(conn, "সুমিতার কত বাকি")["kind"] == "due_query"
    assert parse_command(conn, "কোন মাল শেষ হয়ে আসছে")["report"] == "low_stock"


def test_undo():
    conn = make()
    assert parse_command(conn, "শেষ বিক্রিটা বাতিল করো")["kind"] == "undo"
