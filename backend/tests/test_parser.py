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


def test_joma_with_sho_hundreds():
    # batch-1 sentence 6, as dictated: 'তিনশো' must parse as 300
    conn = make()
    d = parse_command(conn, "করিমদা তিনশো টাকা জমা দিলেন ইউপিআইতে")
    assert d["kind"] == "due_payment" and d["amount"] == 300
    assert d["customer"]["name"] == "করিম"


def test_unknown_item_keeps_tirisha_stated_price():
    # batch-1 sentence 2, as dictated: 'তিরিশ' spelling variant
    conn = make()
    d = parse_command(conn, "একটা তিরিশ টাকার চিপস বিল করো")
    assert d["unknown_items"] == [
        {"text": "চিপস", "qty": 1.0, "stated_price": 30.0}
    ]


def test_fuzzy_customer_from_dictation_spelling():
    # batch-1 sentence 9: dictation wrote 'সুনিতা' for customer সুমিতা
    conn = make()
    d = parse_command(conn, "দুইটা দুধ আর একটা ডিটারজেন্ট সুনিতার নামে বাকিতে")
    assert d["customer"]["name"] == "সুমিতা"
    assert d["payment_type"] == "due" and d["total"] == 159


def test_english_units_are_safe():
    # audit P0: '500 g sugar' used to parse as 500 KG (₹30,000)
    conn = make()
    d = parse_command(conn, "500 g sugar")
    assert d["items"][0]["name_bn"] == "চিনি" and d["items"][0]["qty"] == 0.5
    assert d["total"] == 30
    d = parse_command(conn, "half kg sugar")
    assert d["items"][0]["qty"] == 0.5 and d["total"] == 30
    d = parse_command(conn, "2 dozen eggs")
    assert d["items"][0]["qty"] == 24 and d["total"] == 168


def test_natural_questions_are_queries_not_sales():
    # audit P1: these used to become sales with mangled unknown items
    conn = make()
    d = parse_command(conn, "রহিমের কত টাকা বাকি")
    assert d["kind"] == "due_query" and d["customer"]["name"] == "রহিম"
    d = parse_command(conn, "আজ কত টাকা বিক্রি হয়েছে")
    assert d["kind"] == "report" and d["report"] == "sales_summary"
    d = parse_command(conn, "গত সপ্তাহে কত টাকা বিক্রি হয়েছে")
    assert d["kind"] == "report" and d["report"] == "week_compare"
    d = parse_command(conn, "কোন মাল শেষ")
    assert d["kind"] == "report" and d["report"] == "low_stock"


def test_separator_free_items_all_found():
    # spoken batch 1 #12 / batch 2 #1-3: no commas, no আর between items
    conn = make()
    d = parse_command(conn, "এক কেজি চিনি দুইটা সাবান")
    assert d["total"] == 130 and len(d["items"]) == 2
    d = parse_command(conn, "দুইটা সাবান এক কেজি চিনি")
    assert d["total"] == 130 and len(d["items"]) == 2
    d = parse_command(conn, "এক কেজি চিনি দুইটা সাবান আধা কেজি মসুর ডাল")
    assert d["total"] == 190 and len(d["items"]) == 3


def test_compound_number_quantities():
    conn = make()
    d = parse_command(conn, "দুইশ পঞ্চাশ গ্রাম চিনি")
    assert d["items"][0]["qty"] == 0.25 and d["total"] == 15
    d = parse_command(conn, "সাড়ে পাঁচশ টাকা জমা দিলেন রহিমদা")
    assert d["kind"] == "due_payment" and d["amount"] == 550
    d = parse_command(conn, "দেড় হাজার টাকা জমা দিলেন রহিমদা")
    assert d["kind"] == "due_payment" and d["amount"] == 1500


def test_new_customer_name_cleanup():
    conn = make()
    # genitive must not stick to the name (audit P1-4)
    d = parse_command(conn, "জয় সরকারের নামে দুই কেজি চিনি বাকিতে")
    assert d["customer_name"] == "জয় সরকার"
    # payer named at sentence start, no কে/নামে marker
    d = parse_command(conn, "জয় সরকার ২০০ টাকা জমা দিলেন")
    assert d["kind"] == "due_payment" and d["amount"] == 200
    assert d["customer_name"] == "জয় সরকার"


def test_spoken_batch2_lessons():
    # real second-speaker recordings (spoken batch 2, 27 Oct) exposed:
    conn = make()
    # clipped দুটা for দুইটা
    d = parse_command(conn, "এক কেজি চিনি দুটা সাবান")
    assert d["total"] == 130
    d = parse_command(conn, "দুটা দুধ সুমিতার নামে বাকিতে")
    assert d["total"] == 60 and d["payment_type"] == "due"
    # bare টাকা (no র) still states the price when a product follows
    d = parse_command(conn, "একশো পঞ্চাশ টাকা সরিষার তেল")
    assert d["total"] == 150
    # clipped পনে for পৌনে
    d = parse_command(conn, "পনে দুই কেজি আলু")
    assert d["total"] == 52.5
