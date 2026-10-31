"""Muse Voice Transcribe adapter (POST /v1/asr/transcribe).

Verified 27 Oct 2026 against dev.meta.ai/docs/speech-to-text: the ASR
endpoint is NOT the OpenAI audio API — it takes multipart form data
with a JSON "request" part and an "audio" part holding a WAV file
(mono, 16-bit PCM, 16 kHz or 24 kHz).

Flags (env, read at CALL time so tests/replay can flip them):
  VOICE_PREPROCESS=v0|v1|v2|v3  ffmpeg filter chain (default v0 =
      format conversion only; variants must earn their place via
      scripts/replay_asr.py measurement — MASTER-PLAN MP-20/Gate G2)
  VOICE_SAMPLE_RATE=24000|16000 (default 24000)
  VOICE_BIASING=on|off          send languageBias(list)+keywords
      (default ON since Gate G1, proven 31 Oct by scripts/asr_spike.py:
      list form + keywords return HTTP 200, while the STRING form is
      rejected with 400 — eval/asr-capability.json. Set =off to A/B.)
"""
import json
import os
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .base import TranscribeResult, TranscriptionError

BASE_URL = "https://api.meta.ai/v1"
TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"  # verified via GET /v1/models

_FILTERS = {
    "v0": None,
    # high-pass the fan/AC rumble, trim leading/trailing silence
    "v1": "highpass=f=80,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB",
    # + loudness normalisation so near/far speakers level out
    "v2": "highpass=f=80,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,loudnorm=I=-16:TP=-1.5:LRA=11",
    # + light stationary-noise reduction (only if v2 loses on noisy clips)
    "v3": "highpass=f=80,afftdn=nr=12,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,loudnorm=I=-16:TP=-1.5:LRA=11",
}


def _preprocess() -> str:
    v = (os.getenv("VOICE_PREPROCESS") or "v0").strip().lower()
    return v if v in _FILTERS else "v0"


def _sample_rate() -> int:
    try:
        rate = int(os.getenv("VOICE_SAMPLE_RATE") or 24000)
    except ValueError:
        return 24000
    return rate if rate in (16000, 24000) else 24000


def biasing_enabled() -> bool:
    return (os.getenv("VOICE_BIASING") or "on").strip().lower() != "off"


def ffmpeg_available() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


def _to_wav(audio_bytes: bytes, filename: str) -> bytes:
    """Convert any upload to mono 16-bit PCM WAV via ffmpeg.

    A real WAV (RIFF+WAVE magic) passes through untouched. Filter chain
    per VOICE_PREPROCESS; v0 is the historical format-only behaviour.
    """
    if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        return audio_bytes
    variant = _preprocess()
    args = ["ffmpeg", "-y", "-loglevel", "error", "-i", "in", "-ac", "1",
            "-ar", str(_sample_rate()), "-c:a", "pcm_s16le", "-map_metadata", "-1"]
    if _FILTERS[variant]:
        args += ["-af", _FILTERS[variant]]
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / ("in" + (Path(filename).suffix or ".webm"))
        dst = Path(td) / "out.wav"
        src.write_bytes(audio_bytes)
        args[5] = str(src)
        args.append(str(dst))
        try:
            subprocess.run(args, check=True, capture_output=True, timeout=60)
        except FileNotFoundError as exc:
            raise TranscriptionError(
                "ffmpeg_missing",
                "audio conversion unavailable: ffmpeg not installed on server",
            ) from exc
        except subprocess.SubprocessError as exc:
            raise TranscriptionError(
                "invalid_audio", "audio could not be decoded"
            ) from exc
        return dst.read_bytes()


class MuseTranscriber:
    engine = TRANSCRIBE_MODEL

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    def transcribe(self, audio_bytes: bytes, filename="audio.webm",
                   keywords: list | None = None) -> TranscribeResult:
        if not self.api_key:
            raise TranscriptionError("not_configured", "MODEL_API_KEY not set")
        wav = _to_wav(audio_bytes, filename)
        request_obj: dict = {"model": TRANSCRIBE_MODEL, "audioEncoding": "WAV"}
        if biasing_enabled():
            # List form only (string form is rejected — see module doc).
            request_obj["languageBias"] = ["Bengali", "English"]
            if keywords:
                request_obj["keywords"] = keywords[:200]
        boundary = "----comamasr" + secrets.token_hex(8)
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
        req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TranscriptionError("upstream", f"transcription provider failed: {exc.__class__.__name__}") from exc
        if not isinstance(out, dict):
            raise TranscriptionError("upstream", "unexpected transcription response")
        text = out.get("transcript")
        if not text:
            # ENDPOINTING-style responses may carry per-turn transcripts.
            turns = out.get("turns")
            if isinstance(turns, list):
                text = " ".join(
                    str(t.get("transcript") or "") for t in turns if isinstance(t, dict)
                ).strip()
        return TranscribeResult(
            text=(text or "").strip(),
            alternatives=[],
            confidence=None,
            engine=self.engine,
            raw_keys=sorted(out.keys()),
        )
