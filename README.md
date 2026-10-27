# comam AI — Bengali Voice-First AI Shop Assistant

> Repository: `comam` (created 2026-10-26) · Created 2026-10-26 for the Meta Global AI
> Developer Hackathon — this project is being designed and built in the
> open during the hackathon window, 26 Oct – 4 Nov 2026.

## What this is

A small shopkeeper runs the shop by *speaking Bengali*. Say —

> "দুই কেজি চিনি, আধা কেজি মসুর ডাল, তিনটে সাবান — রহিমদার নামে।"

— and comam AI listens, matches the items to the shop's catalog, builds the bill, reads the total back ("মোট ৩৪০ টাকা — নগদ না বাকি?"), and saves the sale only after the shopkeeper says yes. It also keeps the **বাকি খাতা** (the due ledger every small shop lives by) and answers business questions from real data. The pitch in one line: *যে দোকানি ইংরেজি ট্যাপ-স্ক্রিন ভয় পান, তিনি এখন মুখে বললেই হিসাব হয়ে যায়।*

**Theme:** Build for Your World · **Track:** Indie Developer
**Built for:** Meta Global AI Developer Hackathon, 26 Oct – 4 Nov 2026.

## Core scope (frozen — this, and only this, gets built first)

1. **Voice/text billing.** A spoken (or typed) multi-item bill becomes a cart draft: items matched against the catalog (Bengali names, spelling variants, Banglish), quantities and prices filled in, total read back — cash, due, or UPI? A price spoken with an item ("দশ টাকার বিস্কুট") selects that priced variant. Items the catalog does not know are flagged, never silently dropped. The sale is saved, and stock updated, **only after the shopkeeper confirms**.
2. **বাকি ও জমা (due ledger).** Sell on credit to a named customer, record a payment ("রহিমদা ২০০ টাকা জমা দিলেন"), and ask "সুমিতার কত বাকি?" — the answer comes from the ledger, not the model's memory.
3. **Reports from whitelisted tools.** "গত সপ্তাহের চেয়ে এই সপ্তাহে বিক্রি কেমন?", "কোন মাল শেষ হয়ে আসছে?", "আজ কত বিক্রি হলো?" — answered with a chart plus a one-or-two-line Bengali summary.
4. **Safety nets.** An ambiguous command gets a clarification question, never a guess ("২০ টাকার বড় প্যাকেটটা, না ১০ টাকার ছোটটা?"). "শেষ বিক্রিটা বাতিল করো" undoes the last sale, with an audit-log entry.

### The hard parts (where the technical work lives)

- **Deterministic Bengali number & unit parser** — পঁচাত্তর, সাড়ে তিন, দেড় কেজি, আধা কেজি — parsed by code we write and test, not left to the LLM.
- **Whitelisted tool calling only:** `search_product`, `add_to_cart`, `set_customer`, `record_due_payment`, `get_sales_summary`, `get_low_stock`, `top_products`. The LLM never writes to the database and never does text-to-SQL; it prepares drafts, code commits them after confirmation.
- **An eval set:** ~100 recorded Bengali shop commands (several voices) scored for parse accuracy and final-cart correctness — the results will be reported in this README.

### Stretch (only after the core demo is recorded)

- Photo of a handwritten চালান / shopping list → stock entry (vision).
- SAM 3.1 experiment: counting products from a shelf photo.

Out of scope: GST/billing formats, multiple shops, user accounts. Read-back is on screen; spoken read-back only if TTS proves reliable — the demo never depends on it.

## How it works

```
Mic ─► Muse Voice Transcribe (Bengali) ─► Muse Spark (Standard, tool calling)
     ─► whitelisted tools build a DRAFT (cart / due entry / report)
     ─► confirm screen ─► FastAPI validates ─► SQLite (products, sales, dues, stock)
```

The split is the point: **AI understands language; code does the accounting.** Every total, stock change, and due balance is computed by deterministic code.

## Why not a general-purpose phone agent?

General phone agents can operate almost any app by looking at screenshots and tapping — but every step is a visual guess, and in a shop a wrong tap is *wrong money*, written into the books before anyone notices. comam takes the opposite approach where money is concerned: **general agents guess their way through screens; comam never guesses with money.** The AI only understands the Bengali — totals, stock and dues are computed by deterministic code, nothing is saved before the shopkeeper confirms, and every action is auditable and undoable. That reliability is measured, not claimed (see the eval results below), and it is fast and cheap enough for a hundred bills a day, with an offline parser fallback when the network drops. Nor is comam meant to stay a standalone till: its understanding layer (speech → verified draft) is built to plug into existing POS systems through their APIs — the Bengali voice layer on top of the software shops already use.

## Models used

- **Runtime:** `muse-spark-1.3` (Standard) via the Meta Model API (`https://api.meta.ai/v1`) — understands Bengali instructions and calls the whitelisted tools.
- **Voice in:** Muse Voice Transcribe (Bengali) — quality verified on Day 1 against the number/parser test sentences; typing always remains available, and the confirm step catches misheard numbers.
- **Development:** Muse Code. Contributor-tier models may be used while *writing* this public code; the shipped app itself only ever calls the Standard model above.

## Status & eval

**26 Oct (Day 0):** backend complete (billing spine, due ledger, reports, draft→confirm, ambiguity resolution, editable drafts), frontend built, demo seed data in place. 20 automated tests pass.

**27 Oct (Day 1):** voice verified end-to-end — a real spoken Bengali bill (the founder's own voice note) transcribed cleanly by `muse-voice-transcribe-1.0` and parsed into a draft. That one real sentence exposed four parser gaps, all fixed the same morning: (1) items missing from the catalog (ম্যাগি) are now flagged with the price the shopkeeper stated, never silently dropped, and confirm is blocked until the draft is reviewed; (2) a spoken price picks the variant — "দশ টাকার বিস্কুট" is the ৳10 small pack ×1, not 10 big packs; (3) UPI (ইউপিআই) is a payment type alongside cash and due; (4) a new customer's name is captured and the customer is created on confirm. Spoken-eval batch 1 (12 dictated sentences) then found two more: the তিরিশ spelling variant and তিনশো-type "-শো" hundreds are now recognised, and customer matching tolerates a one-letter dictation misspelling (সুনিতা → সুমিতা) with a clear-winner rule. Re-recorded as real voice and run through Meta ASR, batch 1 scored **11/12** end-to-end. Spoken batch 2 went further: **5 different speakers** (not the founder) × the same 12 sentences = 60 spoken commands through Meta ASR + parser — **53 exact, 1 safely flagged for clarification, 6 failed**. The failures cluster honestly: ASR hears পৌনে ("quarter to") as পনেরো ("fifteen") for 3 of 5 speakers, and repeated words at sentence boundaries garble 3 more; the batch also taught the parser three real-world forms it now handles — clipped দুটা ("two"), bare টাকা as a price marker, and clipped পনে for পৌনে.

**27 Oct (Day 1, hardening):** an external audit probed the app with hostile inputs; every confirmed finding was fixed the same day, with each probe kept as a regression test. Highlights: money guards now sit at the confirm step, so *both* engines are held to them — a due sale without a customer, an empty bill, selling more than the stock, and paying back more than the due are all refused with a Bengali explanation; resolving one ambiguity no longer clears the others; undo voids the sale that was last *when the command was spoken*. The parser also learned separator-free speech ("এক কেজি চিনি দুইটা সাবান" → both items), natural questions ("রহিমের কত টাকা বাকি"), English units ("500 g sugar" = 0.5 kg, not 500 kg), and compound numbers (দুইশ পঞ্চাশ, সাড়ে পাঁচশ, দেড় হাজার). 39 automated tests pass.

**Local-parser eval (offline baseline, `eval/commands.json`, 68 commands):**
- Intent + customer accuracy: **68/68 (100%)**
- Sale/purchase total accuracy: **48/48 (100%)** — totals always computed by code from DB prices
- Cart accuracy (exact items + quantities, where specified): **7/7 (100%)**
- Ambiguity detection: flagged, never guessed (বিস্কুট → two pack sizes)

The same command set will be run through the Muse Spark path and with recorded voices (~100 spoken commands) during the hackathon; results will be added here. Run it yourself: `python scripts/run_eval.py`.

## Tech

- Backend: Python + FastAPI · Database: SQLite
- Frontend: React + Vite + TypeScript (mobile-friendly Bengali UI, charts for reports)
- Money/stock arithmetic in backend code, covered by tests; eval harness for the voice commands.

## New-work disclosure

This project is designed, written, and built entirely during the hackathon (26 Oct – 4 Nov 2026) in this repository — every commit is dated inside that window. The author brings prior *domain experience* from shop bookkeeping software, but no pre-existing code is used or copied; problem knowledge is reused, code is not.

## Demo (exactly 3 minutes)

- 0:00–0:20 — The problem: a small shop keeping accounts by hand, in Bengali; hands busy, screen untouched.
- 0:20–1:05 — Voice bill: three items + a customer's name; catalog match, read-back ("মোট ৩৪০ — নগদ না বাকি?"), confirm, sale saved, stock drops.
- 1:05–1:35 — বাকি: a sale on due to রহিম; "রহিমদা ২০০ টাকা জমা দিলেন"; "রহিমদার কত বাকি?"
- 1:35–2:10 — Report: "গত সপ্তাহের চেয়ে এই সপ্তাহে বিক্রি কেমন?" — chart + Bengali summary; low-stock answer.
- 2:10–2:40 — The hard parts: an ambiguous command gets a clarification question; "সাড়ে তিন কেজি" parsed by the deterministic parser; "শেষ বিক্রিটা বাতিল করো" undoes a sale.
- 2:40–3:00 — Impact for Bengali-speaking shopkeepers; eval numbers; repo link.

## How to run

```bash
# 1) connectivity check (needs your key)
export MODEL_API_KEY="your-key"        # PowerShell: $env:MODEL_API_KEY = "your-key"
python check_api.py

# 2) backend
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cd .. && python scripts/seed_demo.py   # optional: 14 days of demo sales (idempotent)
cd backend && .venv/bin/uvicorn app.main:app --reload   # http://localhost:8000

# 3) frontend (new terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api)

# tests
cd backend && .venv/bin/python -m pytest
```

Without `MODEL_API_KEY` the app still runs: commands are understood by the built-in deterministic Bengali parser. With the key set, Muse Spark (Standard) handles commands through the whitelisted tools, falling back to the local parser if the API fails. The key is never committed (see `.env.example`, `.gitignore`).

## Submission

- Public repo (this one), working app, demo video exactly 3 minutes.
- Submission due 5 Nov 2026, 07:30 IST (4 Nov, 21:00 ET). Aim to submit on the night of 4 Nov, not at the deadline.
