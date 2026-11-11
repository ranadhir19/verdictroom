# Proof runs — 2026-11-10 (attempted, incomplete: free tiers stopped play)

**Headline, stated plainly: no live debate completed.** Three attempts
were made to run sample decision 01 (plugin architecture) end to end.
All three were stopped by the free tiers — Groq rate-limited the very
first call, and Gemini then timed out, returned 503, and returned 429
across the following attempts. This project's policy treats a 429 as
a hard stop for that provider (no retries around it), so once Gemini
also returned a 429, both providers were stopped and the live proof
could not continue in this session.

What follows is the full evidence trail. The engine itself is verified
offline (14 tests, harness green, scripted end-to-end demo), and the
failure handling below was exercised for real — fallbacks, hard stops,
and partial records all fired as designed, and two genuine bugs the
attempts exposed were found and fixed. What was *not* measured, and
is not claimed anywhere: whether a live debate changes positions,
whether a live judge uses cross-examination, and what a live debate
costs in tokens. Those need one completed run, which needs quota.

## Attempt 1 — default cross-provider cast (CLI `decide`)

Cast: Debater A = Groq `openai/gpt-oss-120b`, Debater B = Gemini
`gemini-3.5-flash`, Judge = Groq `qwen/qwen3.8-27b`.

- The baseline call (Groq, gpt-oss-120b) returned **HTTP 429**.
  Per policy, Groq was stopped for the session at that moment and
  never called again.
- The baseline role fell back to its Gemini seat, `gemini-2.5-flash`,
  which returned **404**: that model is "no longer available to new
  users" on the Interactions API surface. The error itself named the
  successor (`gemini-3.8-flash`). Under the code as first written, a
  non-429 failure on a fallback seat aborted the run — bug #1 (fixed:
  a dead seat now falls back once instead of killing the run, and the
  withdrawn 2.5-series models were removed from the default cast's
  fallback seats).

## Attempt 2 — Gemini-only cast (experiments/run_proof.py)

Groq designed around: Debater A = `gemini-3.8-flash`, Debater B =
`gemini-3.5-flash`, Judge = `gemini-3.6-flash`, all fallbacks Gemini.

- Baseline, both openings, and Debater A's cross-examination
  completed (5 calls). Debater B's cross-examination then failed in
  a way consistent with a Gemini 429 (the provider stopped, both
  revision seats — primary and fallback both being that provider —
  became unavailable, and the run aborted with "no debater produced
  a usable final position").
- Lesson recorded: at that point an aborted run threw away its
  ledger and transcript. Fixed: aborts now carry a partial snapshot,
  written to `partial-record.json` (see attempt 3).

## Attempt 3 — Gemini-only cast, instrumented

Full call log (also preserved in
`01-plugin-architecture/partial-record.json`):

| # | Call | Model | Result |
|---|------|-------|--------|
| 1 | baseline | gemini-3.8-flash | read timeout (>150 s) |
| 2 | opening, Debater A | gemini-3.8-flash | HTTP 503 (high demand) |
| 3 | opening, Debater B | gemini-3.5-flash | **HTTP 429** |

- Two more bugs found and fixed from this evidence:
  - **Bug #2 (serious):** the 429 above was *mislabelled* in the
    ledger as a "transport failure", because `HTTPError` subclasses
    `URLError` and the transport handler was ordered first — so the
    hard-stop policy would never have fired on a real 429. Handler
    order fixed; the bug is noted in the code where it lived.
  - **Bug #3:** fallback blocking was per-*provider*, so a failed
    model blocked its fallback seat even when the fallback was a
    different model on the same provider. Blocking is now per-seat
    (provider, model); a 429 still stops the whole provider.
- With those fixed, the run's remaining behaviour was correct but
  moot: Gemini had returned a 429, both providers were stopped, and
  the proof leg ended — by policy, not by discretion.

## What the offline verification does establish

- The protocol runs end to end: baseline → openings → cross-exam →
  revisions → judge → record, 8 calls, hash chain verifies, dissent
  preserved verbatim, position changes detected (scripted provider,
  `python3 validate.py`, all stages PASS; 14 tests).
- Degradation works as specified and is now test-covered: 429 stops
  a provider without retries; a dead seat falls back once; an abort
  preserves its evidence as a partial record.
- Costs so far: $0.00. Token totals for the completed calls of
  attempt 2 were not preserved (pre-partial-record code), so no
  token figure is quoted for the live attempts — an absence recorded
  rather than a number invented.

## To finish the proof (one command, when quota allows)

```bash
python3 experiments/run_proof.py examples/01-plugin-architecture experiments/01-plugin-architecture
python3 experiments/run_proof.py examples/02-premium-pricing    experiments/02-premium-pricing
python3 experiments/run_proof.py examples/03-build-vs-buy-evals experiments/03-build-vs-buy-evals
```

Then this file gains the section it currently lacks: per decision —
baseline vs verdict, who moved and what moved them, whether the
judge's stated reliance matches the cross-examination transcript,
and the token ledger. If a re-run shows the debate merely ratifying
the baseline on all three samples, that is the finding, and it will
be reported as such.

## Continuation attempt — 2026-11-11 (still no live debate)

The three commands above were re-run the next day. **Headline
unchanged: no live debate completed.** The blocker changed shape,
and the difference is recorded rather than smoothed over:

- **Groq was never called.** Earlier in the same work session, a
  sibling project's bench run (gold-at-five) drew **HTTP 429** from
  Groq on its first call; under the standing hard-stop rule Groq
  stayed stopped for the session. `run_proof.py` is Gemini-only by
  construction in any case, so this changed nothing about the cast.
- **Sample 01, attempted twice.** Attempt 1: the baseline's primary
  seat hit a read timeout; its fallback seat (`gemini-3.6-flash`)
  returned **HTTP 503** ("currently experiencing high demand") and
  the run aborted at the baseline call. Attempt 2, after a pause:
  both baseline seats returned HTTP 503. Aborted at baseline.
- **Sample 02, attempted once** after a further pause: both
  baseline seats returned HTTP 503. Aborted at baseline.
- **Sample 03 was not attempted — a decision, recorded as such.**
  It uses the identical cast and the identical baseline seats, and
  by then Gemini had failed on every live call of the session
  across two projects (eight HTTP 503s across the 3.5, 3.6 and 3.8
  flash aliases, plus one read timeout). Running it would have
  produced the same baseline abort; it stays first in the queue
  for the next session instead.
- The aborted runs wrote their partial records as designed
  (`01-plugin-architecture/partial-record.json`,
  `02-premium-pricing/partial-record.json` — each now holds the
  2026-11-11 attempt, superseding the 2026-11-10 one).

So 2026-11-10 was stopped by quota (429) and 2026-11-11 by
sustained provider unavailability (503 on every seat). Neither day
produced a single completed debate call sequence past the baseline.
Costs so far remain $0.00. The claim this file makes is still only
the offline one: the protocol is verified end to end against a
scripted provider; the live question — whether a debate changes
anything — remains unmeasured.
