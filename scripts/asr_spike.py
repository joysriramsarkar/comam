"""Meta ASR capability spike (VOICE-MASTER-PLAN MP-03, Gate G1).

Sends ONE short generated tone clip to POST /v1/asr/transcribe with
different "request" payloads and records which are ACCEPTED (HTTP 200)
vs REJECTED (4xx). A tone proves request validity only — accuracy of
languageBias/keywords needs real speech clips (scripts/replay_asr.py).

This settles, with evidence instead of blog claims:
  U1  languageBias: none vs string vs list
  U2  keywords: accepted at all?
  U3  mode/ENDPOINTING + response keys (does "turns" ever arrive?)

Needs MODEL_API_KEY in .env. Results -> eval/asr-capability.json
Run: backend/.venv/Scripts/python.exe scripts/asr_spike.py
"""
import io
import json
import math
import struct
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.asr.muse import BASE_URL, TRANSCRIBE_MODEL  # noqa: E402
from app.meta_client import MetaClient              # noqa: E402


def _tone_wav(seconds=1.0, rate=24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * t / rate)))
            for t in range(int(seconds * rate))
        )
        w.writeframes(frames)
    return buf.getvalue()


def _send(wav: bytes, request_obj: dict, api_key: str) -> dict:
    boundary = "----spike7f3a"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\n"
        f"Content-Type: application/json\r\n\r\n"
        f"{json.dumps(request_obj, ensure_ascii=False)}\r\n"
    ).encode("utf-8")
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; "
        f"filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + wav + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(BASE_URL + "/asr/transcribe", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8"))
            return {"status": resp.status, "keys": sorted(out.keys()) if isinstance(out, dict) else [], "transcript_preview": str(out.get("transcript"))[:60] if isinstance(out, dict) else ""}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:  # network etc.
        return {"status": None, "error": f"{e.__class__.__name__}: {e}"}


def main() -> int:
    client = MetaClient()
    if not client.enabled:
        print("MODEL_API_KEY not set in .env — spike cannot run")
        return 2
    wav = _tone_wav()
    base = {"model": TRANSCRIBE_MODEL, "audioEncoding": "WAV"}
    variants = {
        "baseline_no_bias": dict(base),
        "languageBias_string": {**base, "languageBias": "Bengali"},
        "languageBias_list": {**base, "languageBias": ["Bengali"]},
        "languageBias_list_en": {**base, "languageBias": ["Bengali", "English"]},
        "keywords": {**base, "keywords": ["চিনি", "কেজি", "পৌনে", "সাড়ে"]},
        "bias_list_plus_keywords": {**base, "languageBias": ["Bengali", "English"], "keywords": ["চিনি", "কেজি"]},
        "mode_endpointing": {**base, "mode": "ENDPOINTING"},
    }
    results = {}
    for name, payload in variants.items():
        r = _send(wav, payload, client.api_key)
        results[name] = r
        verdict = "ACCEPTED" if r.get("status") == 200 else "rejected/unreachable"
        print(f"{name:28s} -> {r.get('status')} ({verdict}) keys={r.get('keys', [])} {r.get('error', '')[:120]}")
    out_path = ROOT / "eval" / "asr-capability.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {out_path}")
    print("Gate G1 rule: only variants with status 200 may be enabled (VOICE_BIASING=on).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
