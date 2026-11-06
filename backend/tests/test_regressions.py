"""Regression tests from the 2026-11 audit fixes.

These run against the same app-level connection the API uses
(main_mod._conn), exactly like test_api.py — never capture that object
at import time, or the two modules silently talk to different DBs.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from app import main as main_mod           # noqa: E402
from app.db import get_conn, init_db       # noqa: E402

main_mod._conn = init_db(get_conn(":memory:"))
client = TestClient(main_mod.app)


def db():
    # The connection the app is using AT CALL TIME (other modules may
    # rebind the global between tests).
    return main_mod._conn


def _clear_pending():
    db().execute("UPDATE drafts SET status='rejected' WHERE status='pending'")
    db().commit()


def test_voice_affirm_failure_never_duplicates_draft():
    _clear_pending()
    # "হ্যাঁ" confirms the latest pending draft — and when the confirm
    # fails (stock), the API must say so. It must NOT fall through and
    # re-save the same bill as a second pending draft.
    db().execute("UPDATE products SET stock=1 WHERE name_en='sugar'")
    db().commit()
    r1 = client.post("/api/command", json={"text": "দশ কেজি চিনি"})
    assert r1.status_code == 200 and r1.json()["kind"] == "sale"
    before = db().execute("SELECT COUNT(*) c FROM drafts").fetchone()["c"]
    r2 = client.post("/api/command", json={"text": "হ্যাঁ"})
    assert r2.status_code == 400
    after = db().execute("SELECT COUNT(*) c FROM drafts").fetchone()["c"]
    assert after == before  # no duplicate draft was created
    db().execute("UPDATE products SET stock=100 WHERE name_en='sugar'")
    db().commit()
    _clear_pending()


def test_stale_pending_draft_not_voice_confirmed():
    _clear_pending()
    # A pending draft older than the 15-minute window must not be
    # committed by a later "হ্যাঁ" — hours-old abandoned bills stay pending.
    r1 = client.post("/api/command", json={"text": "এক কেজি আলু"})
    draft_id = r1.json()["draft_id"]
    db().execute(
        "UPDATE drafts SET created_at = datetime('now', 'localtime', '-2 hours')"
        " WHERE id=?",
        (draft_id,),
    )
    db().commit()
    r2 = client.post("/api/command", json={"text": "হ্যাঁ"})
    assert r2.status_code == 200
    assert r2.json()["kind"] == "unknown"
    row = db().execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()
    assert row["status"] == "pending"
    db().execute("UPDATE drafts SET status='rejected' WHERE id=?", (draft_id,))
    db().commit()


def test_voice_affirm_confirms_recent_pending():
    _clear_pending()
    r1 = client.post("/api/command", json={"text": "এক কেজি আলু"})
    draft_id = r1.json()["draft_id"]
    r2 = client.post("/api/command", json={"text": "হ্যাঁ"})
    assert r2.status_code == 200
    assert r2.json()["kind"] == "confirmed"
    row = db().execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()
    assert row["status"] == "confirmed"


def test_undo_without_pinned_target_is_refused():
    _clear_pending()
    # A hand-planted undo draft with no pinned target must NOT void
    # whatever sale happens to be last when the button is pressed.
    r0 = client.post("/api/command", json={"text": "এক কেজি আলু"})
    sale_draft = r0.json()["draft_id"]
    assert client.post(f"/api/drafts/{sale_draft}/confirm").status_code == 200
    last = db().execute(
        "SELECT id FROM sales WHERE status='confirmed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert last is not None
    payload = json.dumps(
        {"kind": "undo", "ambiguities": [], "raw": "শেষ বিক্রিটা বাতিল করো"}
    )
    cur = db().execute(
        "INSERT INTO drafts (kind, payload) VALUES ('undo', ?)", (payload,)
    )
    db().commit()
    r = client.post(f"/api/drafts/{cur.lastrowid}/confirm")
    assert r.status_code == 400
    row = db().execute("SELECT status FROM sales WHERE id=?", (last["id"],)).fetchone()
    assert row["status"] == "confirmed"  # untouched


def test_report_days_are_clamped():
    for url in (
        "/api/reports/summary?days=-1",
        "/api/reports/summary?days=10000",
        "/api/reports/top?days=0",
        "/api/reports/top?days=99999",
        "/api/reports/daily?days=999",
    ):
        assert client.get(url).status_code == 422, url


def test_command_text_length_capped():
    r = client.post("/api/command", json={"text": "চিনি" * 400})
    assert r.status_code == 422
