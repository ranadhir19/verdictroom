"""Decision briefs and the shared context folder.

A brief is a small JSON document: the question, at least two options,
and the constraints the decision must respect. The "shared brain" of
v1 is deliberately unglamorous: a plain folder of text/markdown files.
Each file gets a stable document id (D1, D2, ...) so debaters can cite
evidence as [D2], and the record can verify which documents were
actually cited.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MAX_DOC_CHARS = 9000
MAX_CONTEXT_CHARS = 26000


def load_brief(path: str | Path) -> dict:
    brief = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(brief.get("question"), str) or not brief["question"]:
        raise ValueError("brief needs a non-empty 'question'")
    options = brief.get("options") or []
    if len(options) < 2:
        raise ValueError("brief needs at least two options")
    ids = set()
    for opt in options:
        if not opt.get("id") or not opt.get("label"):
            raise ValueError("every option needs an 'id' and a 'label'")
        if opt["id"] in ids:
            raise ValueError(f"duplicate option id: {opt['id']}")
        ids.add(opt["id"])
    brief.setdefault("constraints", [])
    brief.setdefault("id", Path(path).stem)
    return brief


def load_context(folder: str | Path | None) -> tuple[list[dict], str, bool]:
    """Return (doc metadata, combined text, truncated flag)."""
    if folder is None:
        return [], "", False
    folder = Path(folder)
    if not folder.exists():
        raise ValueError(f"context folder not found: {folder}")
    docs: list[dict] = []
    chunks: list[str] = []
    total = 0
    truncated = False
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in (".md", ".txt"))
    for idx, path in enumerate(files, start=1):
        raw = path.read_text(encoding="utf-8", errors="replace")
        doc_id = f"D{idx}"
        body = raw
        if len(body) > MAX_DOC_CHARS:
            body = body[:MAX_DOC_CHARS]
            truncated = True
        if total + len(body) > MAX_CONTEXT_CHARS:
            body = body[: max(0, MAX_CONTEXT_CHARS - total)]
            truncated = True
        if not body:
            truncated = True
            continue
        total += len(body)
        docs.append({"id": doc_id, "file": path.name, "chars": len(body),
                     "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()})
        chunks.append(f"[{doc_id}: {path.name}]\n{body}")
    return docs, "\n\n".join(chunks), truncated


def brief_text(brief: dict) -> str:
    lines = [f"DECISION: {brief['question']}"]
    if brief.get("stakes"):
        lines.append(f"STAKES: {brief['stakes']}")
    lines.append("OPTIONS:")
    for opt in brief["options"]:
        lines.append(f"  {opt['id']}. {opt['label']}"
                     + (f" — {opt['summary']}" if opt.get("summary") else ""))
    if brief.get("constraints"):
        lines.append("CONSTRAINTS:")
        for c in brief["constraints"]:
            lines.append(f"  - {c}")
    return "\n".join(lines)
