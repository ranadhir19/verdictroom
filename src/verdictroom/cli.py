"""Command line for verdictroom.

  verdictroom decide --brief BRIEF.json [--context DIR] --out DIR
  verdictroom verify RECORD.json
  verdictroom demo            (offline, scripted fake provider)

`decide` writes record.json (machine-auditable) and record.md (the
same record rendered for humans) into the output directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .brief import load_brief, load_context
from .protocol import Cast, RoleSpec, RunAborted, run_debate
from .providers import FakeProvider
from .record import render_markdown, verify_record


def _spec(base: RoleSpec, text: str) -> RoleSpec:
    provider, _, model = text.partition(":")
    if not model:
        raise SystemExit(f"model spec must be provider:model, got {text!r}")
    return RoleSpec(base.name, provider, model,
                    base.fallback_provider, base.fallback_model,
                    base.lens_name, base.lens_text)


def _cast_from_args(args) -> Cast:
    cast = Cast()
    if args.debater_a:
        cast.debater_a = _spec(cast.debater_a, args.debater_a)
    if args.debater_b:
        cast.debater_b = _spec(cast.debater_b, args.debater_b)
    if args.judge:
        cast.judge = _spec(cast.judge, args.judge)
    return cast


def emit_partial(partial: dict, out: Path, reason: str) -> Path:
    """Write the evidence of an aborted run (no decision was made)."""
    out.mkdir(parents=True, exist_ok=True)
    payload = {"tool": "verdictroom", "status": "aborted",
               "reason": reason, **partial}
    path = out / "partial-record.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def cmd_decide(args) -> int:
    brief = load_brief(args.brief)
    docs, context_text, truncated = load_context(args.context)
    try:
        record = run_debate(brief, docs, context_text, truncated,
                            cast=_cast_from_args(args),
                            include_baseline=not args.no_baseline,
                            log=lambda *a: print(*a, file=sys.stderr))
    except RunAborted as exc:
        print(f"run aborted: {exc}", file=sys.stderr)
        if exc.partial:
            path = emit_partial(exc.partial, Path(args.out), str(exc))
            print(f"partial record (evidence, no decision): {path}",
                  file=sys.stderr)
        return 1
    return _emit(record, Path(args.out))


def _emit(record: dict, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    (out / "record.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "record.md").write_text(render_markdown(record), encoding="utf-8")
    verdict = verify_record(record)
    d = record["decision"]
    print(f"decision: {d['decision']} "
          f"(confidence {d['confidence']['level']})")
    print(f"record: {out / 'record.json'}")
    print(f"chain verifies: {verdict['ok']}")
    for lim in record["limitations"]:
        print(f"limitation: {lim}")
    return 0 if verdict["ok"] else 1


def cmd_verify(args) -> int:
    record = json.loads(Path(args.record).read_text(encoding="utf-8"))
    verdict = verify_record(record)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["ok"] else 1


def cmd_demo(args) -> int:
    brief = {
        "id": "demo-widget",
        "question": "Demo decision: ship the widget behind a flag, or "
                    "hold it for the next release?",
        "options": [
            {"id": "A", "label": "Ship behind a flag"},
            {"id": "B", "label": "Hold for next release"},
        ],
        "constraints": ["Demo only — no real stakes."],
    }
    docs = [{"id": "D1", "file": "demo.md", "chars": 42, "sha256": "demo"}]
    fake_cast = Cast(
        debater_a=RoleSpec("Debater A", "fake", "fake-a", None, None,
                           "Outcomes & evidence", "demo lens"),
        debater_b=RoleSpec("Debater B", "fake", "fake-b", None, None,
                           "Risk, cost & reversibility", "demo lens"),
        judge=RoleSpec("Judge", "fake", "fake-judge"))
    record = run_debate(
        brief, docs, "[D1: demo.md]\nDemo context document.", False,
        cast=fake_cast, clients={"fake": FakeProvider()},
        include_baseline=True, log=lambda *a: None)
    verdict = verify_record(record)
    print(f"demo decision: {record['decision']['decision']}")
    print(f"demo chain verifies: {verdict['ok']}")
    print(f"demo dissent verbatim: "
          f"{record['decision']['dissent_preserved']['verbatim']}")
    print(f"demo calls: {record['ledger_totals']['calls']}")
    return 0 if verdict["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verdictroom")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("decide", help="run a debate and write a record")
    p.add_argument("--brief", required=True)
    p.add_argument("--context", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--debater-a", default=None,
                   help="provider:model override")
    p.add_argument("--debater-b", default=None)
    p.add_argument("--judge", default=None)
    p.add_argument("--no-baseline", action="store_true")
    p.set_defaults(fn=cmd_decide)

    p = sub.add_parser("verify", help="recompute a record's hash chain")
    p.add_argument("record")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("demo", help="offline scripted run")
    p.set_defaults(fn=cmd_demo)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
