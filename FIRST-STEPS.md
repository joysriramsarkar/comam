# First steps — comam AI (build plan, 27 Oct – 4 Nov)

> **Progress, 26 Oct night (ahead of plan):** the whole core is already
> built and verified — backend (billing spine, parser, due ledger,
> reports, ambiguity resolve, editable drafts), frontend (built OK),
> 20 tests passing, local-parser eval 50/50. Remaining real work:
> (a) live Muse Spark test with the real API key, (b) Day-1 Bengali
> voice-transcribe test, (c) spoken eval set (~100 recorded commands),
> (d) demo data/video polish. The day plan below stays as the schedule
> for those remaining pieces.
>
> **Update, 27 Oct:** (a) and (b) done — live Spark tool-calling and
> Bengali voice transcription verified end-to-end. Spoken eval batch 1
> scored 11/12 through Meta ASR. An external audit's confirmed findings
> were fixed the same day (confirm-step money guards, per-ambiguity
> resolve, separator-free parsing, English units, natural questions);
> the suite is now 42 tests passing, local-parser eval 100/100 with
> per-item cart checks (grown from 68; the expansion caught a real
> worth-pricing bug, "এক হাজার টাকার চাল"). Large-quantity drafts now
> warn on the read-back, and the mic falls back to server transcription
> where Web Speech is missing. Remaining: more spoken batches,
> demo video, submission polish.

## Before app code (27 Oct morning)

1. **Repo:** ✅ DONE 26 Oct night — public repo created (named `conin` by
   mistake; the real name is `comam` and the repo will be renamed later)
   and these files pushed as the first commits, so every commit is dated
   inside the hackathon window.
2. **API connectivity:** set `MODEL_API_KEY` and run `check_api.py`
   (in this draft folder). It must print a Bengali answer from
   `muse-spark-1.3` (Standard). If it fails, debug model name / key /
   account access before touching any UI.
3. **Voice test (Day 1, time-boxed):** try Muse Voice Transcribe on
   Bengali shop sentences with hard numbers — পঁচাত্তর, সাড়ে তিন কেজি,
   দেড় কেজি — plus a full 3-item bill. Note exactly where it errs; those
   cases go into the deterministic parser's test list and the eval set.
4. **Credit guardrails:** free credits stood at $137.93 on 26 Oct evening.
   Development in Muse Code defaults to `muse-spark-1.3-contributor`
   (nearly free at ~97% cache hit, fine for this public code). Wonder is
   budgeted — hard problems only, it burned ~$12 in one evening.
   The app runtime calls Standard only.

## Build order

- **27–28 Oct (billing spine):** DB schema (products, customers, sales,
  sale items, due payments, stock); ASR + intent; a spoken/typed bill
  becomes a cart draft end-to-end. No due ledger, no reports yet.
- **29–30 Oct (matching + parser + confirm):** catalog matching (Bengali
  names, spelling variants, Banglish), deterministic number/unit parser
  with tests, confirm-before-write flow, stock update, money-math tests.
- **31 Oct (বাকি খাতা):** due sales to named customers, due payments,
  "কার কত বাকি?" query.
- **1–2 Nov (reports):** whitelisted report tools — sales summary,
  week-vs-week, low stock, top products — chart + Bengali summary lines;
  sample data (7+ days of sales); mobile-friendly polish.
- **3 Nov (eval + demo):** ~100-command Bengali eval set (own voice +
  others'), measure parse/cart accuracy, fix the top failures, record
  the demo. Stretch (photo চালান / SAM shelf count) starts ONLY if the
  core demo is already recorded.
- **4 Nov (buffer + submit):** fresh-clone run-through, README final
  (incl. eval results), final video, repo public + links checked, submit
  at night. Deadline is 5 Nov 07:30 IST — never aim for it.

## Hard rules carried over

- Scope stays at the frozen core in README.md. New ideas go in a
  "later" list, not into the build.
- The LLM only drafts via whitelisted tools — it never writes to the DB,
  never calculates money, never does text-to-SQL. Confirmation commits.
- No old POS code copied in — concepts yes, code no (new-work rule).
- API key only in environment variables; `.env` is gitignored; the key
  never appears in the repo, screenshots, or the demo video.
