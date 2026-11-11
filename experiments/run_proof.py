#!/usr/bin/env python3
"""Proof-run driver for the 2026-11-10 experiments.

The default cross-provider cast could not be used for these runs:
Groq returned HTTP 429 on the very first live call of the day (the
baseline call of run 01, primary seat). Per this project's rate-limit
policy — a 429 is a hard stop, never retried around — Groq was not
called again, and the proof was run on the surviving provider with
three distinct Gemini models in the three seats (fallbacks also
Gemini, so no code path in these runs can reach Groq). The records
carry this as an initial limitation.

Usage: python3 experiments/run_proof.py EXAMPLE_DIR OUT_DIR
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from verdictroom.brief import load_brief, load_context  # noqa: E402
from verdictroom.cli import _emit, emit_partial  # noqa: E402
from verdictroom.protocol import (Cast, RoleSpec, RunAborted,  # noqa: E402
                                  run_debate)

GROQ_NOTE = (
    "Groq returned HTTP 429 on the first live call of this proof "
    "session (baseline, run 01) and, per the project's hard-stop "
    "policy, was not called again; all seats in this run are Gemini "
    "models. Cross-provider debate was therefore NOT exercised in "
    "this proof — only cross-model debate on one provider.")


def proof_cast() -> Cast:
    return Cast(
        debater_a=RoleSpec(
            "Debater A", "gemini", "gemini-3.8-flash",
            "gemini", "gemini-3.6-flash",
            "Outcomes & evidence",
            "weigh what each option measurably delivers, for whom, and "
            "how soon; distrust elegance that has not yet produced a "
            "result."),
        debater_b=RoleSpec(
            "Debater B", "gemini", "gemini-3.5-flash",
            "gemini", "gemini-3.8-flash",
            "Risk, cost & reversibility",
            "weigh what each option costs when it goes wrong, how hard "
            "it is to undo, and what it quietly forecloses."),
        judge=RoleSpec("Judge", "gemini", "gemini-3.6-flash",
                       "gemini", "gemini-3.5-flash"))


def main() -> int:
    example = Path(sys.argv[1])
    out = Path(sys.argv[2])
    brief = load_brief(example / "brief.json")
    docs, context_text, truncated = load_context(example / "context")
    try:
        record = run_debate(brief, docs, context_text, truncated,
                            cast=proof_cast(),
                            initial_limitations=[GROQ_NOTE],
                            log=lambda *a: print(*a, file=sys.stderr))
    except RunAborted as exc:
        print(f"run aborted: {exc}", file=sys.stderr)
        if exc.partial:
            path = emit_partial(exc.partial, out, str(exc))
            print(f"partial record (evidence, no decision): {path}",
                  file=sys.stderr)
        return 1
    return _emit(record, out)


if __name__ == "__main__":
    sys.exit(main())
