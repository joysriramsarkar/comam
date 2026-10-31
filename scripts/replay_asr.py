"""Spoken-eval replay harness (VOICE-MASTER-PLAN MP-01/MP-02, Phase 3).

Replays saved voice clips from eval/audio/ through the CURRENT ASR +
parser settings and scores them separately, so a change (preprocess
variant, biasing, engine) is measured, never guessed:

  WER / CER        raw transcript error (parser not involved)
  number accuracy  number-phrase values heard vs expected (multiset)
  entity accuracy  expected products present in the parsed draft
  exact cart       draft items == expected items (name + qty)

Each clip is a .wav plus a sidecar .json:
  {"file": "s01-001.wav", "expected_text": "দুই কেজি চিনি",
   "expected_items": [{"name_bn": "চিনি", "qty": 2}],
   "speaker": "s01", "device": "android-low", "noise": "quiet",
   "transcript": "..."}        <- optional: score offline, no API call

Without "transcript" the clip is sent to Meta ASR (needs MODEL_API_KEY
in .env). Clips are consented eval recordings only (plan §1.6).

Run: backend/.venv/Scripts/python.exe scripts/replay_asr.py [--json-out r.json]
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.bn import find_number_phrases, normalize_asr  # noqa: E402
from app.db import get_conn, init_db                    # noqa: E402
from app.parser_local import parse_command              # noqa: E402


def _edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r, h = ref.split(), hyp.split()
    return _edit_distance(r, h) / max(1, len(r))


def cer(ref: str, hyp: str) -> float:
    return _edit_distance(list(ref), list(hyp)) / max(1, len(ref))


def _numbers(text: str):
    return Counter(round(v, 3) for v, _, _ in find_number_phrases(text))


def score_case(meta: dict, transcript: str, conn) -> dict:
    expected_text = meta.get("expected_text", "")
    draft = parse_command(conn, normalize_asr(transcript)) if transcript else {}
    exp_items = sorted(
        (i["name_bn"], round(float(i["qty"]), 3)) for i in meta.get("expected_items", [])
    )
    got_items = sorted(
        (i["name_bn"], round(float(i["qty"]), 3)) for i in draft.get("items", [])
    )
    exp_nums, got_nums = _numbers(expected_text), _numbers(transcript)
    num_hits = sum((exp_nums & got_nums).values())
    return {
        "file": meta.get("file"),
        "transcript": transcript,
        "wer": round(wer(expected_text, transcript), 4) if expected_text else None,
        "cer": round(cer(expected_text, transcript), 4) if expected_text else None,
        "number_accuracy": (num_hits / sum(exp_nums.values())) if exp_nums else None,
        "entity_ok": all(any(g[0] == e[0] for g in got_items) for e in exp_items) if exp_items else None,
        "exact_cart": (got_items == exp_items) if exp_items else None,
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "eval" / "audio"))
    ap.add_argument("--json-out")
    args = ap.parse_args()

    audio_dir = Path(args.dir)
    metas = []
    for sidecar in sorted(audio_dir.glob("*.json")):
        metas.append(json.loads(sidecar.read_text(encoding="utf-8")))
    if not metas:
        print(f"no clips in {audio_dir} — see eval/audio/README.md for the format")
        return 0

    conn = init_db(get_conn(":memory:"))
    meta_client = None
    results = []
    for meta in metas:
        transcript = meta.get("transcript")
        if transcript is None:
            if meta_client is None:
                from app.meta_client import MetaClient

                meta_client = MetaClient()
                if not meta_client.enabled:
                    print("MODEL_API_KEY not set and a clip has no stored transcript")
                    return 2
            wav = (audio_dir / meta["file"]).read_bytes()
            transcript = meta_client.transcribe(wav, meta["file"])
        results.append(score_case(meta, transcript, conn))

    def avg(key):
        vals = [r[key] for r in results if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    print(f"clips: {len(results)}")
    for key, label in (("wer", "WER"), ("cer", "CER"), ("number_accuracy", "number accuracy")):
        v = avg(key)
        print(f"{label}: {v:.3f}" if v is not None else f"{label}: n/a")
    for key, label in (("entity_ok", "entity accuracy"), ("exact_cart", "exact cart")):
        vals = [r[key] for r in results if r.get(key) is not None]
        if vals:
            print(f"{label}: {sum(vals)}/{len(vals)} = {sum(vals) / len(vals) * 100:.1f}%")
    bad = [r for r in results if r.get("exact_cart") is False or (r.get("wer") or 0) > 0.3]
    if bad:
        print("\nworst cases:")
        for r in bad[:10]:
            print(f"  - {r['file']} wer={r['wer']} cart={r['exact_cart']}: {r['transcript']}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
