"""Voice-path tests (VOICE-MASTER-PLAN MP-13/MP-23/MP-24, §6.1).

Hermetic like the rest of the suite: MODEL_API_KEY is forced empty by
conftest, so Meta ASR is stubbed where an enabled client is needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod                     # noqa: E402
from app.asr.base import TranscribeResult, TranscriptionError  # noqa: E402
from app.bn import bn_to_ascii, find_number_phrases, normalize_asr  # noqa: E402
from app.catalog import phonetic_key, search_products  # noqa: E402
from app.db import get_conn, init_db                 # noqa: E402

main_mod._conn = init_db(get_conn(":memory:"))
client = TestClient(main_mod.app)
conn = main_mod._conn


# --- normalize_asr: surface artifacts only, number meanings untouched ---

def test_normalize_keeps_heard_numbers_verbatim():
    # The পৌনে/পনেরো rule: what was heard stays; asking happens later.
    assert normalize_asr("পনেরো কেজি চিনি") == "পনেরো কেজি চিনি"
    assert normalize_asr("পৌনে দুই কেজি চাল") == "পৌনে দুই কেজি চাল"
    assert normalize_asr("  দুই   কেজি  চিনি ") == "দুই কেজি চিনি"


def test_normalize_colon_artifact():
    out = normalize_asr("1:10 টাকা দামের বিস্কুট")
    assert "10 টাকা" in out and ":" not in out


def test_devanagari_digits_become_numbers():
    assert bn_to_ascii("५००") == "500"
    vals = [v for v, _, _ in find_number_phrases("५०० গ্রাম চিনি")]
    assert vals == [500.0]


# --- phonetic key: ASR letter-swaps still find the noun ---

def test_phonetic_key_folds_asr_swaps():
    assert phonetic_key("শাবান") == phonetic_key("সাবান")
    assert phonetic_key("মসুর") == phonetic_key("মশুর")


def test_phonetic_search_finds_product():
    matches, _ = search_products(conn, "শাবান")
    assert matches and matches[0][0]["name_bn"] == "সাবান"


# --- blocking quantity alerts: ask, never remap ---

def test_large_quantity_blocks_confirm_until_ack():
    r = client.post("/api/command", json={"text": "পনেরো কেজি চিনি"})
    d = r.json()
    assert d["needs_clarification"] is True
    alerts = d["draft"]["alerts"]
    assert len(alerts) == 1 and alerts[0]["qty"] == 15  # heard 15 stays 15
    assert d["draft"]["alerts_ack"] is False
    assert "পৌনে" in alerts[0]["message_bn"]
    blocked = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert blocked.status_code == 400
    ack = client.post(f"/api/drafts/{d['draft_id']}/alerts/ack")
    assert ack.status_code == 200 and ack.json()["draft"]["alerts_ack"] is True
    assert ack.json()["draft"]["items"][0]["qty"] == 15
    ok = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert ok.status_code == 200


def test_ordinary_quantity_has_no_alert():
    r = client.post("/api/command", json={"text": "দুই কেজি চিনি"})
    d = r.json()
    assert d["draft"]["alerts"] == [] and d["draft"]["alerts_ack"] is True
    assert d["needs_clarification"] is False


def test_editing_qty_upwards_cannot_bypass_alert():
    r = client.post("/api/command", json={"text": "দুই কেজি চিনি"})
    d = r.json()
    item = d["draft"]["items"][0]
    r2 = client.put(
        f"/api/drafts/{d['draft_id']}/items",
        json={"items": [{"product_id": item["product_id"], "qty": 12}]},
    )
    edited = r2.json()["draft"]
    assert len(edited["alerts"]) == 1 and edited["alerts_ack"] is False
    blocked = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert blocked.status_code == 400
    client.post(f"/api/drafts/{d['draft_id']}/alerts/ack")
    ok = client.post(f"/api/drafts/{d['draft_id']}/confirm")
    assert ok.status_code == 200


# --- /api/transcribe validation & error taxonomy ---

def _stubbed_client(monkeypatch, fn):
    monkeypatch.setattr(main_mod.meta, "enabled", True)
    monkeypatch.setattr(main_mod.meta, "transcribe_detailed", fn)
    return TestClient(main_mod.app)


def test_transcribe_disabled_without_key():
    r = client.post("/api/transcribe", files={"file": ("a.webm", b"x")})
    assert r.status_code == 501


def test_transcribe_rejects_empty_and_oversize(monkeypatch):
    c = _stubbed_client(monkeypatch, lambda *a: TranscribeResult(text="x"))
    r = c.post("/api/transcribe", files={"file": ("a.webm", b"")})
    assert r.status_code == 400
    big = b"x" * (main_mod.MAX_TRANSCRIBE_BYTES + 1)
    r = c.post("/api/transcribe", files={"file": ("a.webm", big)})
    assert r.status_code == 413


def test_transcribe_enriched_response_and_normalization(monkeypatch):
    def fake(data, filename, keywords=None):
        return TranscribeResult(text="1:10 টাকা", engine="test-engine")

    c = _stubbed_client(monkeypatch, fake)
    r = c.post("/api/transcribe", files={"file": ("a.webm", b"1234")})
    body = r.json()
    assert r.status_code == 200
    assert body["text"] == "10 টাকা"  # colon artifact cleaned at the door
    assert body["engine"] == "test-engine"
    assert body["warnings"] == [] and body["confidence"] is None


def test_transcribe_no_speech_warning(monkeypatch):
    c = _stubbed_client(
        monkeypatch, lambda *a: TranscribeResult(text="", engine="t")
    )
    r = c.post("/api/transcribe", files={"file": ("a.webm", b"1234")})
    assert r.json()["warnings"] == ["no_speech"]


def test_transcribe_ffmpeg_missing_is_503(monkeypatch):
    def boom(data, filename, keywords=None):
        raise TranscriptionError("ffmpeg_missing", "no ffmpeg")

    c = _stubbed_client(monkeypatch, boom)
    r = c.post("/api/transcribe", files={"file": ("a.webm", b"1234")})
    assert r.status_code == 503


def test_health_reports_voice_path():
    body = client.get("/api/health").json()
    assert "ffmpeg_available" in body and "voice_preprocess" in body
