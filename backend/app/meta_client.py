"""Meta Model API client (Muse Spark, Standard tier) + voice transcription.

If MODEL_API_KEY is not set, enabled=False and the API layer falls back to
the deterministic local parser — the app (and its tests) runs fully offline.
The shipped app must always use the Standard model here, never -contributor.
"""
import json
import os

from .tools import SYSTEM_PROMPT, TOOL_SCHEMAS, DraftSession, customers_brief, dispatch

BASE_URL = "https://api.meta.ai/v1"
CHAT_MODEL = "muse-spark-1.3"          # Standard — runtime model
TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"  # exact id verified 27 Oct via GET /v1/models


class MetaClient:
    def __init__(self):
        self.api_key = os.getenv("MODEL_API_KEY")
        self.enabled = bool(self.api_key)
        self._client = None

    def _get(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=BASE_URL, api_key=self.api_key)
        return self._client

    def understand(self, conn, text):
        """Run tool-calling against a DraftSession; returns the draft dict.
        Raises on API failure so the caller can fall back to the local parser."""
        client = self._get()
        session = DraftSession(conn)
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    customers=json.dumps(customers_brief(conn), ensure_ascii=False)
                ),
            },
            {"role": "user", "content": text},
        ]
        for _ in range(6):
            resp = client.chat.completions.create(
                model=CHAT_MODEL, messages=messages, tools=TOOL_SCHEMAS
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                draft = session.to_draft(raw=text)
                draft["reply"] = msg.content or ""
                return draft
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = dispatch(session, tc.function.name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        draft = session.to_draft(raw=text)
        draft["reply"] = ""
        return draft

    def transcribe_detailed(self, audio_bytes: bytes, filename="audio.webm",
                            keywords: list | None = None):
        """Full ASR result via the provider adapter (app/asr/).

        This method keeps the historical contract in one place; the
        adapter owns wire format, ffmpeg preprocessing (VOICE_PREPROCESS)
        and optional biasing (VOICE_BIASING, Gate G1). Note from 27 Oct
        verification: do NOT send a languageBias STRING — the API rejects
        it as malformed; the adapter sends the list form, flag-gated.
        """
        from .asr.manager import get_transcriber

        return get_transcriber(self.api_key).transcribe(
            audio_bytes, filename, keywords=keywords
        )

    def transcribe(self, audio_bytes: bytes, filename="audio.webm",
                   keywords: list | None = None) -> str:
        """Back-compatible text-only transcription."""
        return self.transcribe_detailed(audio_bytes, filename, keywords).text
