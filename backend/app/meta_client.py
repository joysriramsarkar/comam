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
TRANSCRIBE_MODEL = "muse-voice-transcribe"  # verify exact id on Day 1


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

    def transcribe(self, audio_bytes: bytes, filename="audio.webm"):
        """Experimental: Bengali speech-to-text. Model id + audio format
        support must be verified on Day 1 (see FIRST-STEPS.md)."""
        client = self._get()
        import io

        f = io.BytesIO(audio_bytes)
        f.name = filename
        resp = client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)
        return resp.text
