"""The verdictroom debate protocol.

Cast: two debaters on different models from different providers, each
arguing through a different lens, and a judge on a third model that
did not debate. A single-call baseline (the strongest debater's model,
no debate) runs first, so the record can show what the debate itself
added — or honestly show that it added nothing.

Provider failure policy: a 429 stops that provider for the rest of the
run (no retries around it). Roles fall back to their alternate
provider/model where one is configured; a debater with no surviving
provider is recorded as unavailable and the debate continues without
it. Every degradation lands in the record's `limitations`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import prompts
from .brief import brief_text
from .providers import FakeProvider, ProviderError, RateLimited, get_client
from .record import ChainLog, Ledger


class RunAborted(RuntimeError):
    """The run could not produce a decision. `partial` carries the
    ledger, transcript-so-far, and limitations, so an aborted run is
    still auditable — its evidence is written to partial-record.json."""

    def __init__(self, message: str, partial: dict | None = None):
        super().__init__(message)
        self.partial = partial


@dataclass
class RoleSpec:
    name: str
    provider: str
    model: str
    fallback_provider: str | None = None
    fallback_model: str | None = None
    lens_name: str = ""
    lens_text: str = ""


@dataclass
class Cast:
    debater_a: RoleSpec = field(default_factory=lambda: RoleSpec(
        "Debater A", "groq", "openai/gpt-oss-120b",
        "gemini", "gemini-3.8-flash",
        "Outcomes & evidence",
        "weigh what each option measurably delivers, for whom, and how "
        "soon; distrust elegance that has not yet produced a result."))
    debater_b: RoleSpec = field(default_factory=lambda: RoleSpec(
        "Debater B", "gemini", "gemini-3.5-flash",
        "groq", "openai/gpt-oss-20b",
        "Risk, cost & reversibility",
        "weigh what each option costs when it goes wrong, how hard it "
        "is to undo, and what it quietly forecloses."))
    judge: RoleSpec = field(default_factory=lambda: RoleSpec(
        "Judge", "groq", "qwen/qwen3.8-27b",
        "gemini", "gemini-3.6-flash"))


_PHASE_TOKENS = {"baseline": 900, "opening": 1900, "crossexam": 1900,
                 "revision": 1400, "judge": 2400}


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("no JSON object in reply")


class Runner:
    def __init__(self, cast: Cast, clients: dict | None = None,
                 log=lambda *a: None):
        self.cast = cast
        self.clients = clients or {}
        self.log = log
        self.stopped: set[str] = set()
        self.limitations: list[str] = []
        self.ledger = Ledger()
        self.chain = ChainLog()
        self._last_call_at = 0.0

    def snapshot(self) -> dict:
        return {"transcript": self.chain.entries,
                "chain_head": self.chain.head,
                "ledger": self.ledger.entries,
                "ledger_totals": self.ledger.totals(),
                "limitations": self.limitations,
                "providers_stopped": sorted(self.stopped)}

    def _pace(self, client) -> None:
        """Free tiers cap requests per minute; serial debate calls are
        spaced at least ~6s apart. Pacing is not retrying: a 429 still
        stops the provider outright."""
        if isinstance(client, FakeProvider):
            return
        import time as _time
        wait = 6.0 - (_time.monotonic() - self._last_call_at)
        if wait > 0:
            _time.sleep(wait)
        self._last_call_at = _time.monotonic()

    def client(self, provider: str):
        if provider not in self.clients:
            self.clients[provider] = get_client(provider)
        return self.clients[provider]

    def resolve(self, spec: RoleSpec, role_label: str,
                block: frozenset = frozenset()):
        """Return (provider, model) for a role.

        A 429 stops a whole provider (quota is an account property).
        A per-call block, by contrast, blocks one SEAT — a
        (provider, model) pair — because a fallback seat may be a
        different model on the same provider (a withdrawn model must
        not take its whole provider down with it)."""
        primary_seat = (spec.provider, spec.model)
        if spec.provider not in self.stopped and primary_seat not in block:
            return spec.provider, spec.model
        fallback_seat = (spec.fallback_provider, spec.fallback_model)
        if (spec.fallback_provider
                and spec.fallback_provider not in self.stopped
                and fallback_seat not in block):
            note = (f"{role_label}: primary seat "
                    f"{spec.provider}:{spec.model} unusable; fell back "
                    f"to {spec.fallback_provider}:{spec.fallback_model}")
            if note not in self.limitations:
                self.limitations.append(note)
            return spec.fallback_provider, spec.fallback_model
        return None, None

    def ask_json(self, phase: str, role_label: str, spec: RoleSpec,
                 system: str, user: str, required: tuple[str, ...],
                 valid_choices: set[str] | None = None,
                 choice_key: str = "choice",
                 _block: frozenset = frozenset()) -> dict | None:
        provider, model = self.resolve(spec, role_label, _block)
        if provider is None:
            self.chain.append("unavailable", {
                "role": role_label, "phase": phase,
                "reason": "no provider for this role is usable"})
            self.log(f"  {role_label}/{phase}: no usable provider")
            return None
        temperature = 0.2 if role_label in ("Judge", "Baseline") else 0.4
        client = self.client(provider)
        prompt_user = user
        last_error = ""
        for attempt in (1, 2):
            try:
                self._pace(client)
                result = client.chat(system, prompt_user, model,
                                     max_tokens=_PHASE_TOKENS[phase],
                                     temperature=temperature)
            except RateLimited:
                self.log(f"  {role_label}/{phase}: {provider} "
                         "rate-limited (429) — provider stopped")
                self.stopped.add(provider)
                self.ledger.add_failure(phase, role_label, provider, model,
                                        "HTTP 429 — provider stopped for "
                                        "the rest of this run")
                self.limitations.append(
                    f"provider {provider} rate-limited (429) during "
                    f"{phase}; stopped for the rest of the run")
                if attempt == 1:
                    return self.ask_json(phase, role_label, spec, system,
                                         user, required, valid_choices,
                                         choice_key, _block)
                return None
            except ProviderError as exc:
                self.log(f"  {role_label}/{phase}: {provider} error: "
                         f"{str(exc)[:160]}")
                self.ledger.add_failure(phase, role_label, provider, model,
                                        str(exc))
                if not _block:
                    # A dead seat (e.g. a model withdrawn for this
                    # account) is not a dead run: try the fallback seat
                    # once, with the primary blocked for this call.
                    note = (f"{role_label}: {provider}:{model} failed "
                            f"({str(exc)[:120]}); trying the fallback seat")
                    if note not in self.limitations:
                        self.limitations.append(note)
                    return self.ask_json(phase, role_label, spec, system,
                                         user, required, valid_choices,
                                         choice_key,
                                         _block | {(provider, model)})
                raise
            self.ledger.add(phase, role_label, provider, model, result)
            try:
                obj = extract_json(result.text)
                missing = [k for k in required if k not in obj]
                if missing:
                    raise ValueError(f"missing keys: {missing}")
                if valid_choices is not None and \
                        obj.get(choice_key) not in valid_choices:
                    raise ValueError(
                        f"{choice_key}={obj.get(choice_key)!r} is not one "
                        f"of {sorted(valid_choices)}")
                return obj
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                self.log(f"  {role_label}/{phase}: bad reply "
                         f"({last_error}); one repair retry")
                prompt_user = (user + "\n\nYour previous reply could not "
                               f"be used: {last_error}. Reply again with "
                               "ONLY the JSON object requested.")
        self.chain.append("reply_unusable", {
            "role": role_label, "phase": phase, "error": last_error})
        self.log(f"  {role_label}/{phase}: reply unusable after repair "
                 f"({last_error})")
        return None


def _render_entries(entries: list[dict]) -> str:
    parts = []
    for e in entries:
        parts.append(f"--- {e['kind']} ---\n"
                     + json.dumps(e["payload"], indent=1, ensure_ascii=False))
    return "\n".join(parts)


def run_debate(brief: dict, docs: list[dict], context_text: str,
               context_truncated: bool, cast: Cast | None = None,
               clients: dict | None = None, include_baseline: bool = True,
               log=lambda *a: None,
               initial_limitations: list[str] | None = None) -> dict:
    cast = cast or Cast()
    r = Runner(cast, clients=clients, log=log)
    if initial_limitations:
        r.limitations.extend(initial_limitations)
    try:
        return _phases(r, brief, docs, context_text, context_truncated,
                       cast, include_baseline, log)
    except RunAborted as exc:
        if exc.partial is None:
            exc.partial = r.snapshot()
        raise
    except ProviderError as exc:
        raise RunAborted(f"provider failure: {exc}",
                         r.snapshot()) from exc


def _phases(r: Runner, brief: dict, docs: list[dict], context_text: str,
            context_truncated: bool, cast: Cast, include_baseline: bool,
            log) -> dict:
    btx = brief_text(brief)
    option_ids = {o["id"] for o in brief["options"]}
    debaters = [cast.debater_a, cast.debater_b]
    if context_truncated:
        r.limitations.append("context folder exceeded the size budget "
                             "and was truncated (see doc metadata)")
    r.chain.append("brief", {"brief": brief, "context_docs": docs})

    baseline = None
    if include_baseline:
        log("baseline: single call, no debate")
        base_spec = RoleSpec("Baseline", cast.debater_a.provider,
                             cast.debater_a.model,
                             cast.debater_a.fallback_provider,
                             cast.debater_a.fallback_model)
        obj = r.ask_json("baseline", "Baseline", base_spec,
                         prompts.system_for("baseline"),
                         prompts.baseline_user(btx, context_text),
                         required=("choice", "rationale"),
                         valid_choices=option_ids)
        if obj:
            baseline = {"choice": obj["choice"],
                        "rationale": obj.get("rationale", ""),
                        "model": f"{base_spec.provider}:{base_spec.model}"}
            r.chain.append("baseline", baseline)

    openings: dict[str, dict] = {}
    for spec in debaters:
        log(f"opening: {spec.name} ({spec.lens_name})")
        obj = r.ask_json("opening", spec.name, spec,
                         prompts.system_for("debater", spec.lens_name,
                                            spec.lens_text),
                         prompts.opening_user(btx, context_text,
                                              spec.lens_name),
                         required=("choice", "argument"),
                         valid_choices=option_ids)
        if obj:
            obj = {"debater": spec.name, "lens": spec.lens_name, **obj}
            openings[spec.name] = obj
            r.chain.append("opening", obj)
    if not openings:
        raise RunAborted("no debater produced a usable opening")

    openings_tx = _render_entries(
        [e for e in r.chain.entries if e["kind"] == "opening"])
    crossexams: dict[str, dict] = {}
    for spec in debaters:
        if spec.name not in openings:
            continue
        log(f"crossexam: {spec.name}")
        obj = r.ask_json("crossexam", spec.name, spec,
                         prompts.system_for("debater", spec.lens_name,
                                            spec.lens_text),
                         prompts.crossexam_user(
                             btx, openings_tx, spec.lens_name,
                             openings[spec.name]["choice"]),
                         required=("strongest_opposing", "attack",
                                   "would_change_my_mind"))
        if obj:
            obj = {"debater": spec.name, **obj}
            crossexams[spec.name] = obj
            r.chain.append("crossexam", obj)

    transcript_tx = _render_entries(
        [e for e in r.chain.entries
         if e["kind"] in ("opening", "crossexam")])
    revisions: dict[str, dict] = {}
    for spec in debaters:
        if spec.name not in openings:
            continue
        log(f"revision: {spec.name}")
        obj = r.ask_json("revision", spec.name, spec,
                         prompts.system_for("debater", spec.lens_name,
                                            spec.lens_text),
                         prompts.revision_user(
                             btx, transcript_tx, spec.lens_name,
                             openings[spec.name]["choice"]),
                         required=("choice", "final_argument"),
                         valid_choices=option_ids)
        if obj:
            obj = {"debater": spec.name, **obj}
            revisions[spec.name] = obj
            r.chain.append("revision", obj)
    if not revisions:
        raise RunAborted("no debater produced a usable final position")

    full_tx = _render_entries(
        [e for e in r.chain.entries
         if e["kind"] in ("opening", "crossexam", "revision")])
    log("judge: deciding")
    jobj = r.ask_json("judge", "Judge", cast.judge,
                      prompts.system_for("judge"),
                      prompts.judge_user(btx, full_tx),
                      required=("decision", "rationale"),
                      valid_choices=option_ids, choice_key="decision")
    if not jobj:
        raise RunAborted("the judge produced no usable decision")

    decision_id = jobj["decision"]
    dissenters = [name for name, rev in revisions.items()
                  if rev["choice"] != decision_id]
    judge_dissent = jobj.get("dissent") or {}
    quote = judge_dissent.get("quote") or ""
    named = judge_dissent.get("debater") or ""
    dissent_preserved: dict
    if dissenters:
        if named in dissenters and quote and \
                quote in revisions[named].get("final_argument", ""):
            dissent_preserved = {"debater": named, "quote": quote,
                                 "verbatim": True, "source": "judge quote, "
                                 "verified against the transcript"}
        else:
            who = dissenters[0]
            dissent_preserved = {
                "debater": who,
                "quote": revisions[who].get("final_argument", ""),
                "verbatim": True,
                "source": "preserved by the record from the transcript "
                          "(the judge's own quote did not verify verbatim)"}
    elif quote:
        dissent_preserved = {"debater": named, "quote": quote,
                             "verbatim": False,
                             "source": "no dissenting final position; "
                                       "judge-quoted residual-risk passage, "
                                       "not verbatim-verified"}
    else:
        dissent_preserved = {"debater": None, "quote": "", "verbatim": False,
                             "source": "no dissent and no quoted passage"}

    movements = []
    for spec in debaters:
        if spec.name in openings and spec.name in revisions:
            rev = revisions[spec.name]
            movements.append({
                "debater": spec.name,
                "opening": openings[spec.name]["choice"],
                "final": rev["choice"],
                "changed": bool(rev.get("changed")) or
                           rev["choice"] != openings[spec.name]["choice"],
                "what_changed_it": rev.get("what_changed_it", "")})

    cited = sorted({c for e in r.chain.entries
                    if e["kind"] in ("opening", "crossexam", "revision",
                                     "decision")
                    for c in re.findall(r"\b(D\d+)\b",
                                        json.dumps(e["payload"],
                                                   ensure_ascii=False))})
    conf = jobj.get("confidence") or {}
    decision = {
        "decision": decision_id,
        "rationale": jobj.get("rationale", ""),
        "cross_examination_relied_on":
            jobj.get("cross_examination_relied_on", []),
        "dissent_preserved": dissent_preserved,
        "judge_dissent_as_returned": judge_dissent,
        "confidence": {"level": conf.get("level", "unstated"),
                       "why": conf.get("why", "")},
        "overturn_conditions": jobj.get("overturn_conditions", []),
        "unresolved_questions": jobj.get("unresolved_questions", []),
        "judge_model": f"{cast.judge.provider}:{cast.judge.model}",
    }
    r.chain.append("decision", decision)

    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "tool": "verdictroom",
        "record_id": f"{brief['id']}-{created.replace(':', '').replace('-', '')}",
        "created_utc": created,
        "brief": brief,
        "context_docs": docs,
        "cast": {
            "debaters": [{"name": s.name, "lens": s.lens_name,
                          "provider": s.provider, "model": s.model}
                         for s in debaters],
            "judge": {"provider": cast.judge.provider,
                      "model": cast.judge.model},
        },
        "baseline": baseline,
        "transcript": r.chain.entries,
        "chain_head": r.chain.head,
        "movements": movements,
        "decision": decision,
        "documents_cited": cited,
        "ledger": r.ledger.entries,
        "ledger_totals": r.ledger.totals(),
        "limitations": r.limitations,
    }
    return record
