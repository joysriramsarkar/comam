# comam AI — Bengali Voice-First AI Shop Assistant

> Repository: `conin` (created 2026-10-26; will be renamed `comam`) · Created 2026-10-26 for the Meta Global AI
> Developer Hackathon — this project is being designed and built in the
> open during the hackathon window, 26 Oct – 4 Nov 2026.

## What this is

A small shopkeeper runs the shop by *speaking Bengali*. Say —

> "দুই কেজি চিনি, আধা কেজি মসুর ডাল, তিনটে সাবান — রহিমদার নামে।"

— and comam AI listens, matches the items to the shop's catalog, builds the bill, reads the total back ("মোট ৩৪০ টাকা — নগদ না বাকি?"), and saves the sale only after the shopkeeper says yes. It also keeps the **বাকি খাতা** (the due ledger every small shop lives by) and answers business questions from real data. The pitch in one line: *যে দোকানি ইংরেজি ট্যাপ-স্ক্রিন ভয় পান, তিনি এখন মুখে বললেই হিসাব হয়ে যায়।*

**Theme:** Build for Your World · **Track:** Indie Developer
**Built for:** Meta Global AI Developer Hackathon, 26 Oct – 4 Nov 2026.

## Core scope (frozen — this, and only this, gets built first)

1. **Voice/text billing.** A spoken (or typed) multi-item bill becomes a cart draft: items matched against the catalog (Bengali names, spelling variants, Banglish), quantities and prices filled in, total read back — cash or due? The sale is saved, and stock updated, **only after the shopkeeper confirms**.
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

## Models used

- **Runtime:** `muse-spark-1.3` (Standard) via the Meta Model API (`https://api.meta.ai/v1`) — understands Bengali instructions and calls the whitelisted tools.
- **Voice in:** Muse Voice Transcribe (Bengali) — quality verified on Day 1 against the number/parser test sentences; typing always remains available, and the confirm step catches misheard numbers.
- **Development:** Muse Code. Contributor-tier models may be used while *writing* this public code; the shipped app itself only ever calls the Standard model above.

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
python test_api.py

# 2) backend
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cd .. && python scripts/seed_demo.py   # optional: 7 days of demo sales
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
