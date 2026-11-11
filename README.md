# verdictroom

**A decision-debate engine: two models argue, a third decides, and the
whole thing is written into a record you can audit.**

Give verdictroom a decision brief — a question, the options, the
constraints — plus a folder of context documents. It then runs a
structured debate:

1. **Baseline.** One model answers the brief alone, in a single call.
   This is kept, so the record can show what the debate added — or
   honestly show that it added nothing.
2. **Opening.** Two debaters, on different models from different
   providers and arguing through different lenses (outcomes & evidence;
   risk, cost & reversibility), independently pick an option. Each must
   argue from cited context evidence, name the risks of its *own*
   choice, and state the assumption its case rests on.
3. **Cross-examination.** Each debater must attack the strongest
   opposing argument, concede what it cannot answer, and state in
   advance what evidence would change its mind.
4. **Revision.** Each debater restates its final position. Changing
   position is allowed and honoured: the debater must name the exact
   argument that moved it.
5. **Judgement.** A judge — a third model, one that did not debate —
   decides. It must name the cross-examination points it relied on,
   preserve the losing side's final argument as verbatim dissent,
   state its confidence, and state what evidence would overturn its
   decision.

The output is a **decision record** (`record.json` + `record.md`):

- a **hash-chained transcript** — edit any argument after the fact and
  `verdictroom verify` fails;
- the **dissent, preserved verbatim** from the transcript itself,
  never paraphrased by the judge;
- a **cost ledger** of every call (provider, model requested and
  actually served, tokens, latency). Runs use free tiers, so the cost
  is $0.00 — and is still logged, because a decision you cannot price
  is a decision you cannot audit;
- the **movements**: who opened where, who moved, and what moved them;
- the **overturn conditions**: what evidence would flip the decision.

## Why not just ask one model?

Because a single answer hides everything that matters about a hard
decision: the strongest counter-argument, the assumption the case
rests on, and the conditions under which the answer would change.
verdictroom's debate makes those explicit *before* the decision, and
its record keeps them attached to the decision afterwards. Where the
debate adds nothing over the baseline, the record shows that too —
the baseline and the verdict sit side by side.

## Quick start

```bash
PYTHONPATH=src python3 -m verdictroom demo            # offline, scripted
PYTHONPATH=src python3 -m verdictroom decide \
    --brief examples/01-plugin-architecture/brief.json \
    --context examples/01-plugin-architecture/context \
    --out runs/plugin-architecture
PYTHONPATH=src python3 -m verdictroom verify runs/plugin-architecture/record.json
```

Live runs use the free tiers of Groq and Google AI Studio through the
registered connector credential seam (no keys in this repo or in any
record). Debater A defaults to Groq `openai/gpt-oss-120b`, Debater B
to Gemini `gemini-3.5-flash`, the judge to Groq `qwen/qwen3.8-27b`;
override any seat with `--debater-a provider:model` etc. A provider
that returns HTTP 429 is stopped for the rest of the run — never
retried around — and the affected roles fall back to their alternate
provider, with the limitation written into the record.

## The brief format

```json
{
  "id": "my-decision",
  "question": "What exactly is being decided?",
  "stakes": "Why it matters.",
  "options": [
    {"id": "A", "label": "First option", "summary": "One line."},
    {"id": "B", "label": "Second option", "summary": "One line."}
  ],
  "constraints": ["A rule the decision must respect."]
}
```

Context is any folder of `.md` / `.txt` files. Files are numbered
D1, D2, … in filename order and debaters cite them as `[D2]`.

## Honest limits

- Two debaters and one judge is a panel, not a parliament: the point
  is structured disagreement, not scale.
- Models can only argue from the context they are given; a thin
  context folder produces a confident-sounding debate about very
  little. The record shows exactly which documents were cited.
- The judge is a model, with a model's failure modes. The record is
  the mitigation: rationale, dissent, and overturn conditions stay
  attached to the decision for a human to overrule.
- Free-tier rate limits are real. The engine degrades to one provider
  rather than failing, and says so in the record.

## Validation

```bash
python3 validate.py   # compile, tests, offline demo, tamper check, scans
python3 -m pytest tests/ -q
```

## License

MIT — see `LICENSE`.
