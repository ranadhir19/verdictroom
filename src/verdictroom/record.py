"""The decision record: cost ledger, hash-chained transcript, verdict.

A verdictroom record is built to be audited later by someone who was
not in the room:

  * every model call is in the ledger (provider, model requested and
    actually served, tokens, latency, $0.00 on the free tiers);
  * every transcript entry is hash-chained to the previous one, so
    editing any argument after the fact breaks verification;
  * the dissent is preserved verbatim from the transcript itself —
    never paraphrased by the judge;
  * the record renders to Markdown for humans and stays JSON for
    machines; `verify` recomputes the chain from the JSON alone.
"""

from __future__ import annotations

import hashlib
import json
import re

GENESIS = "verdictroom-genesis"


class Ledger:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, phase: str, role: str, provider: str,
            model_requested: str, result, status: str = "ok",
            note: str = "") -> None:
        self.entries.append({
            "seq": len(self.entries) + 1,
            "phase": phase, "role": role, "provider": provider,
            "model_requested": model_requested,
            "model_served": getattr(result, "model_served", None),
            "prompt_tokens": getattr(result, "prompt_tokens", None),
            "completion_tokens": getattr(result, "completion_tokens", None),
            "thought_tokens": getattr(result, "thought_tokens", None),
            "cost_usd": 0.0,
            "latency_s": getattr(result, "latency_s", None),
            "status": status, "note": note,
        })

    def add_failure(self, phase: str, role: str, provider: str,
                    model_requested: str, error: str) -> None:
        self.entries.append({
            "seq": len(self.entries) + 1,
            "phase": phase, "role": role, "provider": provider,
            "model_requested": model_requested, "model_served": None,
            "prompt_tokens": None, "completion_tokens": None,
            "thought_tokens": None, "cost_usd": 0.0, "latency_s": None,
            "status": "failed", "note": error[:300],
        })

    def totals(self) -> dict:
        ok = [e for e in self.entries if e["status"] == "ok"]
        return {
            "calls": len(self.entries),
            "calls_ok": len(ok),
            "prompt_tokens": sum(e["prompt_tokens"] or 0 for e in ok),
            "completion_tokens": sum(e["completion_tokens"] or 0 for e in ok),
            "total_tokens": sum((e["prompt_tokens"] or 0)
                                + (e["completion_tokens"] or 0) for e in ok),
            "cost_usd": 0.0,
        }


class ChainLog:
    """Append-only transcript with a SHA-256 hash chain."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self._prev = GENESIS

    def append(self, kind: str, payload: dict) -> dict:
        entry = {"seq": len(self.entries) + 1, "kind": kind,
                 "payload": payload, "prev": self._prev}
        entry["hash"] = _hash_entry(entry)
        self.entries.append(entry)
        self._prev = entry["hash"]
        return entry

    @property
    def head(self) -> str:
        return self._prev


def _hash_entry(entry: dict) -> str:
    canon = json.dumps({k: entry[k] for k in ("seq", "kind", "payload", "prev")},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def verify_record(record: dict) -> dict:
    """Recompute the chain and the dissent check from a record dict."""
    problems: list[str] = []
    prev = GENESIS
    for entry in record.get("transcript", []):
        if entry.get("prev") != prev:
            problems.append(f"entry {entry.get('seq')}: prev link broken")
        if _hash_entry(entry) != entry.get("hash"):
            problems.append(f"entry {entry.get('seq')}: hash mismatch")
        prev = entry.get("hash")
    if record.get("chain_head") != prev:
        problems.append("chain_head does not match recomputed head")
    dissent = record.get("decision", {}).get("dissent_preserved") or {}
    quote_ok = None
    if dissent.get("quote") and dissent.get("verbatim"):
        finals = {e["payload"].get("debater"): e["payload"]
                  for e in record.get("transcript", [])
                  if e["kind"] == "revision"}
        fin = finals.get(dissent.get("debater")) or {}
        quote_ok = dissent["quote"] in (fin.get("final_argument") or "")
        if not quote_ok:
            problems.append("preserved dissent is not verbatim in the "
                            "cited debater's final argument")
    return {"ok": not problems, "problems": problems,
            "dissent_verbatim": quote_ok}


_CITE_RE = re.compile(r"\[(D\d+)\]")


def citations_in(text: str) -> list[str]:
    return sorted(set(_CITE_RE.findall(text or "")))


def render_markdown(record: dict) -> str:
    b = record["brief"]
    d = record["decision"]
    out: list[str] = []
    out.append(f"# Decision record — {b['question']}")
    out.append("")
    out.append(f"- Record id: `{record['record_id']}`  ")
    out.append(f"- Created: {record['created_utc']}  ")
    out.append(f"- Chain head: `{record['chain_head']}`  ")
    out.append(f"- Verification: `verdictroom verify` recomputes the "
               "hash chain from record.json")
    if record.get("limitations"):
        out.append("- Limitations: " + "; ".join(record["limitations"]))
    out.append("")
    out.append("## Decision")
    out.append("")
    out.append(f"**Chosen: {d['decision']}** — "
               f"{_label(record, d['decision'])}")
    out.append("")
    out.append(f"Confidence: {d['confidence']['level']} — "
               f"{d['confidence'].get('why', '')}")
    out.append("")
    out.append(d["rationale"])
    out.append("")
    out.append("### Cross-examination the judge relied on")
    for item in d.get("cross_examination_relied_on", []):
        out.append(f"- **{item.get('by')}**: {item.get('point')}")
    out.append("")
    out.append("### Dissent, preserved verbatim")
    dis = d.get("dissent_preserved") or {}
    if dis.get("quote"):
        out.append(f"> {dis['quote']}")
        out.append(f"> — {dis.get('debater')}, final argument "
                   f"(verbatim: {dis.get('verbatim')})")
    else:
        out.append("No dissenting final position: the judge's decision "
                   "matched every debater's final choice.")
    out.append("")
    out.append("### What would overturn this decision")
    for c in d.get("overturn_conditions", []):
        out.append(f"- {c}")
    if d.get("unresolved_questions"):
        out.append("")
        out.append("### Left unresolved")
        for q in d["unresolved_questions"]:
            out.append(f"- {q}")
    out.append("")
    out.append("## How the debate moved")
    for mv in record.get("movements", []):
        out.append(f"- **{mv['debater']}**: opening {mv['opening']} → "
                   f"final {mv['final']} "
                   f"({'moved' if mv['changed'] else 'held'})"
                   + (f" — {mv['what_changed_it']}" if mv.get("what_changed_it") else ""))
    if record.get("baseline"):
        out.append(f"- Single-call baseline (no debate): "
                   f"{record['baseline']['choice']} — "
                   f"{record['baseline']['rationale']}")
    out.append("")
    out.append("## Full transcript")
    for e in record["transcript"]:
        out.append("")
        out.append(f"### {e['seq']}. {e['kind']}")
        out.append("")
        out.append("```json")
        out.append(json.dumps(e["payload"], indent=2, ensure_ascii=False))
        out.append("```")
    out.append("")
    out.append("## Cost ledger (free tiers, $0.00)")
    t = record["ledger_totals"]
    out.append(f"- Calls: {t['calls']} ({t['calls_ok']} ok) · "
               f"tokens: {t['total_tokens']} "
               f"({t['prompt_tokens']} in / {t['completion_tokens']} out) · "
               f"cost: $0.00")
    for e in record["ledger"]:
        out.append(f"- #{e['seq']} {e['phase']}/{e['role']} "
                   f"{e['provider']}:{e['model_served'] or e['model_requested']} "
                   f"[{e['status']}] tok={e['prompt_tokens']}/"
                   f"{e['completion_tokens']} {e['latency_s']}s"
                   + (f" — {e['note']}" if e.get("note") else ""))
    out.append("")
    return "\n".join(out)


def _label(record: dict, option_id: str) -> str:
    for opt in record["brief"]["options"]:
        if opt["id"] == option_id:
            return opt["label"]
    return option_id
