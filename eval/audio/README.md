# Spoken eval clips (VOICE-MASTER-PLAN Phase 0/3)

One `.wav` + one sidecar `.json` per clip:

```json
{
  "file": "s01-001.wav",
  "expected_text": "দুই কেজি চিনি, আধা কেজি মসুর ডাল",
  "expected_items": [{"name_bn": "চিনি", "qty": 2}, {"name_bn": "মসুর ডাল", "qty": 0.5}],
  "speaker": "s01",
  "device": "android-low",
  "distance_cm": 30,
  "noise": "quiet | fan | shop"
}
```

Rules:

- **Consented recordings only.** Never a real customer's conversation.
- Add `"transcript": "..."` to score offline (no API call) — e.g. a
  transcript saved from a real failure (MP-34 failure logging).
- Score with `scripts/replay_asr.py`. WER/CER are computed on the raw
  transcript; never score an AI-corrected transcript as ASR output.
- Dataset growth: 10 speakers × 30 sentences (300 clips) first, then
  toward 1000. Categories: numbers (পঁচাত্তর/তিনশো), fractions
  (পৌনে/সাড়ে/দেড়/আধা), product names, customer names, code-mix
  ("500 g sugar", UPI), due/payment, fast speech, regional accents.
