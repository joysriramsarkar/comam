"""Common ASR result / error types."""
from dataclasses import dataclass, field


class TranscriptionError(Exception):
    """ASR failure with a machine-readable kind for HTTP mapping.

    kind: one of "ffmpeg_missing" (deploy problem -> 503),
    "invalid_audio" (the upload itself -> 400), "upstream" (the
    provider failed -> 502), "not_configured" (-> 501).
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class TranscribeResult:
    text: str
    alternatives: list = field(default_factory=list)
    confidence: float | None = None
    engine: str = ""
    # Response keys seen from the provider — capability evidence for
    # Gate G1 (e.g. whether a "turns" field ever actually arrives).
    raw_keys: list = field(default_factory=list)
