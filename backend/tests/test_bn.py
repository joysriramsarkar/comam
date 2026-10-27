import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.bn import find_number_phrases, fmt_taka, parse_number_at  # noqa: E402


def p(text):
    v, _ = parse_number_at(text.split(), 0)
    return v


def test_word_numbers():
    assert p("পঁচাত্তর") == 75
    assert p("দুই") == 2
    assert p("বিশ") == 20
    assert p("নিরানব্বই") == 99


def test_fractions():
    assert p("সাড়ে তিন") == 3.5
    assert p("দেড়") == 1.5
    assert p("আড়াই") == 2.5
    assert p("আধা") == 0.5
    assert p("পৌনে চার") == 3.75
    assert p("সোয়া দুই") == 2.25


def test_digits_and_compounds():
    assert p("৫০০") == 500
    assert p("২.৫") == 2.5
    assert p("একশো পঞ্চাশ") == 150
    assert p("দুই হাজার") == 2000


def test_find_phrases():
    found = find_number_phrases("দুই কেজি চিনি আর ৫০০ গ্রাম ডাল")
    assert [v for v, _, _ in found] == [2.0, 500.0]


def test_fmt():
    assert fmt_taka(285) == "২৮৫ টাকা"
    assert fmt_taka(2.5) == "২.৫ টাকা"


def test_spelling_variants_from_dictation():
    # variants observed in real dictation (spoken-eval batch 1, 27 Oct)
    assert find_number_phrases("তিরিশ টাকা")[0][0] == 30
    assert find_number_phrases("তিনশো টাকা")[0][0] == 300
    assert find_number_phrases("পাঁচশো টাকা")[0][0] == 500
    assert find_number_phrases("দুইশো টাকা")[0][0] == 200


def test_compound_hundreds_and_large_fractions():
    # audit probes (27 Oct): composed hundreds and fraction-of-unit forms
    assert find_number_phrases("দুইশ পঞ্চাশ")[0][0] == 250
    assert find_number_phrases("দুইশো পঞ্চাশ")[0][0] == 250
    assert find_number_phrases("সাড়ে পাঁচশ")[0][0] == 550
    assert find_number_phrases("সাড়ে তিন হাজার")[0][0] == 3500
    assert find_number_phrases("দেড় হাজার")[0][0] == 1500
    assert find_number_phrases("আড়াই হাজার")[0][0] == 2500
    assert find_number_phrases("সাড়ে চার")[0][0] == 4.5
    assert p("half") == 0.5
