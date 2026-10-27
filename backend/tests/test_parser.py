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


VOICE_TRANSCRIPT = (
    "জয় সরকারকে তিনটা ডিম, একটা পনেরো টাকার ম্যাগি আর একটা দশ টাকা দামের "
    "ফার্মের বিস্কুট বিল করো। ইউপিআইতে।"
)


def test_voice_transcript_bill():
    # the founder's real Day-1 voice note, as transcribed by
    # muse-voice-transcribe-1.0 (27 Oct 2026) — all four parser fixes meet here
    conn = make()
    d = parse_command(conn, VOICE_TRANSCRIPT)
    assert d["kind"] == "sale"
    assert d["payment_type"] == "upi"
    assert d["customer_name"] == "জয় সরকার"
    by_name = {i["name_bn"]: i for i in d["items"]}
    assert by_name["ডিম"]["qty"] == 3 and by_name["ডিম"]["line_total"] == 21
    # the price hint picks the 10-taka small pack, qty 1 — not 10 big packs
    assert by_name["বিস্কুট ছোট প্যাকেট"]["qty"] == 1
    assert d["total"] == 31
    assert not d["ambiguities"]
    # ম্যাগি is not in the catalog: flagged with its stated price, never dropped
    assert d["unknown_items"] == [
        {"text": "ম্যাগি", "qty": 1.0, "stated_price": 15.0}
    ]


def test_price_hint_disambiguates_biscuit():
    conn = make()
    d = parse_command(conn, "দশ টাকা দামের বিস্কুট")
    assert d["items"][0]["name_bn"] == "বিস্কুট ছোট প্যাকেট"
    assert d["total"] == 10 and not d["ambiguities"]


def test_takar_weight_fallback_kept():
    conn = make()
    d = parse_command(conn, "৭৫ টাকার চিনি")
    assert d["items"][0]["qty"] == 1.25 and d["total"] == 75
