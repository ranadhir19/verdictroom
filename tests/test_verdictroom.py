"""Offline tests for verdictroom — no network, scripted fake provider."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from verdictroom import brief as brief_mod  # noqa: E402
from verdictroom.brief import load_brief, load_context  # noqa: E402
from verdictroom.protocol import Cast, RoleSpec, extract_json, run_debate  # noqa: E402
from verdictroom.providers import FakeProvider, RateLimited  # noqa: E402
from verdictroom.record import ChainLog, Ledger, verify_record  # noqa: E402


def _brief(tmp_path, **over):
    data = {
        "id": "t1",
        "question": "Pick a door?",
        "options": [{"id": "A", "label": "Door A"},
                    {"id": "B", "label": "Door B"}],
        "constraints": ["Be quick."],
    }
    data.update(over)
    p = tmp_path / "brief.json"
    p.write_text(json.dumps(data))
    return load_brief(p)


def _fake_cast():
    return Cast(
        debater_a=RoleSpec("Debater A", "fake", "fake-a", None, None,
                           "Outcomes & evidence", "lens a"),
        debater_b=RoleSpec("Debater B", "fake", "fake-b", None, None,
                           "Risk, cost & reversibility", "lens b"),
        judge=RoleSpec("Judge", "fake", "fake-judge"))


# ---- brief & context -------------------------------------------------

def test_brief_requires_two_options(tmp_path):
    with pytest.raises(ValueError):
        _brief(tmp_path, options=[{"id": "A", "label": "only"}])


def test_brief_rejects_duplicate_ids(tmp_path):
    with pytest.raises(ValueError):
        _brief(tmp_path, options=[{"id": "A", "label": "x"},
                                  {"id": "A", "label": "y"}])


def test_context_ids_and_hashes(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "b-notes.md").write_text("second file")
    (ctx / "a-first.txt").write_text("first file")
    (ctx / "ignored.png").write_bytes(b"\x89PNG")
    docs, text, truncated = load_context(ctx)
    assert [d["id"] for d in docs] == ["D1", "D2"]
    assert docs[0]["file"] == "a-first.txt"
    assert "[D2: b-notes.md]" in text
    assert truncated is False
    assert all(len(d["sha256"]) == 64 for d in docs)


def test_context_truncation_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(brief_mod, "MAX_DOC_CHARS", 10)
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "big.md").write_text("x" * 500)
    docs, text, truncated = load_context(ctx)
    assert truncated is True
    assert docs[0]["chars"] == 10


# ---- parsing ----------------------------------------------------------

def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('prose {"a": 1} tail') == {"a": 1}
    with pytest.raises(Exception):
        extract_json("no json here")


# ---- ledger & chain ---------------------------------------------------

def test_ledger_totals():
    led = Ledger()

    class R:
        model_served = "m"
        prompt_tokens = 10
        completion_tokens = 5
        thought_tokens = None
        latency_s = 0.5

    led.add("opening", "Debater A", "groq", "m", R())
    led.add_failure("judge", "Judge", "groq", "m", "boom")
    totals = led.totals()
    assert totals["calls"] == 2 and totals["calls_ok"] == 1
    assert totals["total_tokens"] == 15 and totals["cost_usd"] == 0.0


def test_chain_detects_tampering():
    chain = ChainLog()
    chain.append("opening", {"debater": "A", "argument": "original"})
    entry = dict(chain.entries[0])
    entry["payload"] = {"debater": "A", "argument": "edited later"}
    record = {"transcript": [entry], "chain_head": chain.head,
              "decision": {}}
    verdict = verify_record(record)
    assert verdict["ok"] is False
    assert any("hash mismatch" in p for p in verdict["problems"])


# ---- full protocol on the fake provider -------------------------------

def _run(tmp_path, clients=None, cast=None):
    brief = _brief(tmp_path)
    return run_debate(brief, [{"id": "D1", "file": "d.md", "chars": 1,
                               "sha256": "x" * 64}],
                      "[D1: d.md]\ncontext", False,
                      cast=cast or _fake_cast(),
                      clients=clients or {"fake": FakeProvider()})


def test_full_debate_record_verifies(tmp_path):
    record = _run(tmp_path)
    verdict = verify_record(record)
    assert verdict["ok"] is True, verdict
    assert verdict["dissent_verbatim"] is True
    assert record["decision"]["decision"] == "A"
    assert record["ledger_totals"]["calls"] == 8  # 1+2+2+2+1
    assert record["ledger_totals"]["cost_usd"] == 0.0
    assert record["baseline"]["choice"] == "A"
    assert record["documents_cited"] == ["D1"]
    moves = {m["debater"]: m for m in record["movements"]}
    assert moves["Debater A"]["changed"] is False
    assert moves["Debater B"]["final"] == "B"  # dissent preserved for B
    assert record["decision"]["dissent_preserved"]["debater"] == "Debater B"


def test_position_change_is_detected(tmp_path):
    script = {("revision", "Risk, cost & reversibility"): json.dumps({
        "choice": "A", "changed": True,
        "what_changed_it": "the attack on my key assumption",
        "final_argument": "I now back A.",
        "residual_risks": ["still one risk"]})}
    record = _run(tmp_path, clients={"fake": FakeProvider(script=script)})
    moves = {m["debater"]: m for m in record["movements"]}
    assert moves["Debater B"]["changed"] is True
    assert moves["Debater B"]["final"] == "A"
    # nobody dissents from the decision now
    assert record["decision"]["dissent_preserved"]["debater"] is None or \
        record["decision"]["dissent_preserved"]["verbatim"] is False
    assert verify_record(record)["ok"] is True


def test_record_tampering_breaks_verification(tmp_path):
    record = _run(tmp_path)
    record["transcript"][2]["payload"]["argument"] = "rewritten after the fact"
    assert verify_record(record)["ok"] is False


def test_dead_seat_falls_back_once(tmp_path):
    from verdictroom.providers import ProviderError
    cast = Cast(
        debater_a=RoleSpec("Debater A", "dead", "m-a",
                           "fake", "fake-a", "Outcomes & evidence", "l"),
        debater_b=RoleSpec("Debater B", "fake", "fake-b",
                           None, None, "Risk, cost & reversibility", "l"),
        judge=RoleSpec("Judge", "fake", "fake-judge"))
    clients = {
        "dead": FakeProvider(fail_with=ProviderError("dead: HTTP 404")),
        "fake": FakeProvider(),
    }
    record = _run(tmp_path, clients=clients, cast=cast)
    assert record["decision"]["decision"] == "A"
    assert any("fell back" in lim for lim in record["limitations"])
    assert any(e["status"] == "failed" and e["provider"] == "dead"
               for e in record["ledger"])
    assert verify_record(record)["ok"] is True


def test_abort_carries_partial_evidence(tmp_path):
    from verdictroom.protocol import RunAborted
    script = {("judge",): "this is not json"}
    with pytest.raises(RunAborted) as ei:
        _run(tmp_path, clients={"fake": FakeProvider(script=script)})
    partial = ei.value.partial
    assert partial is not None
    kinds = [e["kind"] for e in partial["transcript"]]
    assert "opening" in kinds and "revision" in kinds
    assert partial["ledger_totals"]["calls"] >= 7


def test_seat_block_is_per_model_not_per_provider(tmp_path):
    # A withdrawn model on a provider must not kill the provider:
    # the fallback seat is a different model on the SAME provider.
    cast = Cast(
        debater_a=RoleSpec("Debater A", "fake", "dead-model",
                           "fake", "fake-a", "Outcomes & evidence", "l"),
        debater_b=RoleSpec("Debater B", "fake", "fake-b",
                           None, None, "Risk, cost & reversibility", "l"),
        judge=RoleSpec("Judge", "fake", "fake-judge"))
    clients = {"fake": FakeProvider(fail_models=frozenset({"dead-model"}))}
    record = _run(tmp_path, clients=clients, cast=cast)
    assert record["decision"]["decision"] == "A"
    assert any("fell back" in lim for lim in record["limitations"])
    assert verify_record(record)["ok"] is True


def test_rate_limit_degrades_not_retries(tmp_path):
    cast = Cast(
        debater_a=RoleSpec("Debater A", "groq", "m-a",
                           "gemini", "g-a", "Outcomes & evidence", "l"),
        debater_b=RoleSpec("Debater B", "gemini", "g-b",
                           "groq", "m-b", "Risk, cost & reversibility", "l"),
        judge=RoleSpec("Judge", "groq", "m-j", "gemini", "g-j"))
    clients = {
        "groq": FakeProvider(fail_with=RateLimited("custom.groq")),
        "gemini": FakeProvider(),
    }
    record = _run(tmp_path, clients=clients, cast=cast)
    assert record["decision"]["decision"] == "A"
    assert any("rate-limited" in lim for lim in record["limitations"])
    failed = [e for e in record["ledger"] if e["status"] == "failed"]
    assert failed and all("429" in e["note"] for e in failed)
    assert verify_record(record)["ok"] is True
