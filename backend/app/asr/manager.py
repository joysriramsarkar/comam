"""ASR provider selection (VOICE_ENGINE flag, read at call time)."""
import os

from .base import TranscriptionError
from .muse import MuseTranscriber


def engine_name() -> str:
    return (os.getenv("VOICE_ENGINE") or "meta").strip().lower()


def get_transcriber(api_key: str | None):
    name = engine_name()
    if name in ("meta", "muse"):
        return MuseTranscriber(api_key)
    # Benchmark adapters (Sarvam/Google/Whisper) plug in here after the
    # Gate G4 comparison — never before it (MASTER-PLAN §4.4/Phase 3).
    raise TranscriptionError("not_configured", f"unknown VOICE_ENGINE: {name}")
