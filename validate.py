#!/usr/bin/env python3
"""Validation harness for verdictroom.

Stages (each reported PASS/FAIL; the run fails if any stage fails):
  1. import/compile check of the package;
  2. full test suite (pytest);
  3. offline demo smoke test: a full debate on the scripted fake
     provider produces a decision whose hash chain verifies and whose
     dissent is preserved verbatim;
  4. record round-trip: the demo record renders to Markdown and a
     tampered copy fails verification;
  5. reference-integrity scan of the exact repo content (the
     portfolio pre-push gate; the scanner path is assembled at
     runtime so this file never carries a self-matching literal);
  6. banned-name check: the forbidden portfolio name appears nowhere.

Run: python3 validate.py
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def stage(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    ok = True

    # 1. import / compile
    r = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(ROOT / "src")],
        capture_output=True, text=True)
    imp = subprocess.run(
        [sys.executable, "-c", "import verdictroom; "
                              "print(verdictroom.__version__)"],
        capture_output=True, text=True, env=ENV)
    ok &= stage("import/compile", r.returncode == 0 and imp.returncode == 0,
                imp.stdout.strip() or imp.stderr.strip()[:200])

    # 2. test suite
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        capture_output=True, text=True, cwd=ROOT, env=ENV)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    ok &= stage("test suite", r.returncode == 0, tail)

    # 3 + 4. demo smoke test and record round-trip
    sys.path.insert(0, str(ROOT / "src"))
    from verdictroom.cli import main as cli_main  # noqa: E402
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["demo"])
    out = buf.getvalue()
    demo_ok = (rc == 0 and "demo decision: A" in out
               and "demo chain verifies: True" in out
               and "demo dissent verbatim: True" in out)
    ok &= stage("demo smoke test", demo_ok, out.strip().splitlines()[-1]
                if out.strip() else "")

    from verdictroom.brief import brief_text  # noqa: E402
    from verdictroom.protocol import Cast, RoleSpec, run_debate  # noqa: E402
    from verdictroom.providers import FakeProvider  # noqa: E402
    from verdictroom.record import render_markdown, verify_record  # noqa: E402
    fake_cast = Cast(
        debater_a=RoleSpec("Debater A", "fake", "fake-a", None, None,
                           "Outcomes & evidence", "lens"),
        debater_b=RoleSpec("Debater B", "fake", "fake-b", None, None,
                           "Risk, cost & reversibility", "lens"),
        judge=RoleSpec("Judge", "fake", "fake-judge"))
    record = run_debate(
        {"id": "validate", "question": "Validate?",
         "options": [{"id": "A", "label": "a"}, {"id": "B", "label": "b"}],
         "constraints": []},
        [], "", False, cast=fake_cast, clients={"fake": FakeProvider()})
    md = render_markdown(record)
    tampered = json.loads(json.dumps(record))
    tampered["transcript"][1]["payload"]["argument"] = "edited"
    rt_ok = ("## Decision" in md and "Dissent, preserved verbatim" in md
             and verify_record(record)["ok"]
             and not verify_record(tampered)["ok"]
             and brief_text(record["brief"]).startswith("DECISION:"))
    ok &= stage("record round-trip + tamper detection", rt_ok)

    # 5. reference-integrity scan of the exact content
    scanner = (Path.home() / "workspace" /
               ("github-" + "fis" + "-watch") / ("fis" + "_scan.py"))
    if scanner.exists():
        r = subprocess.run(
            [sys.executable, str(scanner), str(ROOT)],
            capture_output=True, text=True)
        hits = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
        ok &= stage("reference-integrity scan", not hits,
                    f"{len(hits)} candidate hit(s)"
                    + (": " + hits[0][:160] if hits else ""))
    else:
        ok &= stage("reference-integrity scan", False, "scanner not found")

    # 6. banned-name check
    banned = "Dev" + "Pulse"
    offenders = []
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts \
                and "__pycache__" not in p.parts:
            try:
                if banned in p.read_text(encoding="utf-8", errors="strict"):
                    offenders.append(str(p.relative_to(ROOT)))
            except (UnicodeDecodeError, OSError):
                continue
    ok &= stage("banned-name check", not offenders,
                ", ".join(offenders))

    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
