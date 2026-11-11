"""Provider clients for verdictroom.

Two live providers, both on free tiers, both reached through the
registered custom connectors' credential-surrogate seam (the same seam
the connector helpers use): the surrogate places the credential on the
request at call time, so no key value is ever read, stored, or logged
by this package.

  * Groq    — OpenAI-compatible chat completions (api.groq.com).
  * Gemini  — Google AI Studio Interactions API
              (generativelanguage.googleapis.com). Generation runs on
              POST /v1beta/interactions; the served model reported in
              the response is recorded, because the requested alias and
              the served model can differ.

Rate-limit policy (deliberate): an HTTP 429 raises RateLimited and the
caller marks that provider stopped for the rest of the run. There is
no retry around a 429 — the run degrades to the surviving provider and
records the limitation in the decision record.

A scripted FakeProvider supports offline tests and the demo.
"""

from __future__ import annotations

import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

_SURROGATE_DIR = "/opt/muse/skills/custom-connector-setup/bin"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class ProviderError(RuntimeError):
    """A provider call failed in a way the run may survive."""


class RateLimited(ProviderError):
    """HTTP 429. Hard stop for this provider for the rest of the run."""

    def __init__(self, provider: str):
        super().__init__(f"{provider}: rate limited (HTTP 429)")
        self.provider = provider


@dataclass
class ChatResult:
    text: str
    model_served: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    thought_tokens: int | None = None
    latency_s: float = 0.0


def _surrogate():
    if _SURROGATE_DIR not in sys.path:
        sys.path.insert(0, _SURROGATE_DIR)
    try:
        from dynamic_credentials import add_surrogate_to_request, read_json_response
    except Exception as exc:  # pragma: no cover - environment problem
        raise ProviderError(
            "credential surrogate seam unavailable; live calls must run "
            "inside the Muse connector environment") from exc
    return add_surrogate_to_request, read_json_response


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def _post(url: str, payload: dict, connector: str, host: str,
          timeout: int = 150) -> dict:
    add_surrogate, read_json = _surrogate()
    headers = {"Content-Type": "application/json", "User-Agent": _UA,
               "Accept": "application/json"}
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers,
        method="POST")
    add_surrogate(request, connector, allowed_hosts=[host])
    opener = urllib.request.build_opener(_NoRedirect())
    started = time.time()
    try:
        with opener.open(request, timeout=timeout) as response:
            body = read_json(response)
    except urllib.error.HTTPError as exc:
        # NOTE: HTTPError subclasses URLError, so this handler must
        # precede the transport handler below — otherwise a 429 would
        # be misread as a generic transport failure and the hard-stop
        # policy would never fire. (That misordering shipped in v0.1
        # and was caught by the 2026-11-10 proof runs.)
        if exc.code == 429:
            raise RateLimited(connector) from exc
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise ProviderError(f"{connector}: HTTP {exc.code} {detail}") from exc
    except (TimeoutError, ConnectionError, urllib.error.URLError,
            http.client.HTTPException) as exc:
        # Transport failure (free-tier endpoints can stall past the
        # read timeout). Surfaced as ProviderError so the caller can
        # fall back to another seat; never silently retried.
        raise ProviderError(
            f"{connector}: transport failure "
            f"({type(exc).__name__}: {exc})") from exc
    body["_latency_s"] = round(time.time() - started, 3)
    return body


class GroqClient:
    name = "groq"
    BASE = "https://api.groq.com/openai/v1"

    def chat(self, system: str, user: str, model: str,
             max_tokens: int = 1600, temperature: float = 0.4) -> ChatResult:
        body = _post(self.BASE + "/chat/completions", {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }, "custom.groq", "api.groq.com")
        choice = (body.get("choices") or [{}])[0]
        usage = body.get("usage") or {}
        return ChatResult(
            text=(choice.get("message") or {}).get("content") or "",
            model_served=body.get("model") or model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_s=body.get("_latency_s", 0.0))


class GeminiClient:
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def chat(self, system: str, user: str, model: str,
             max_tokens: int = 1600, temperature: float = 0.4) -> ChatResult:
        # The Interactions API takes a single input string for this
        # account class; the system brief is inlined with a delimiter.
        model_id = model if model.startswith("models/") else "models/" + model
        body = _post(self.BASE + "/interactions", {
            "model": model_id,
            "input": f"[BRIEF]\n{system}\n\n[TASK]\n{user}",
        }, "custom.google-ai-studio", "generativelanguage.googleapis.com")
        text = ""
        for step in body.get("steps") or []:
            if step.get("type") == "model_output":
                for part in step.get("content") or []:
                    if part.get("type") == "text":
                        text += part.get("text", "")
        usage = body.get("usage") or {}
        return ChatResult(
            text=text,
            model_served=(body.get("model") or model_id).removeprefix("models/"),
            prompt_tokens=usage.get("total_input_tokens"),
            completion_tokens=usage.get("total_output_tokens"),
            thought_tokens=usage.get("total_thought_tokens"),
            latency_s=body.get("_latency_s", 0.0))


_CLIENTS = {"groq": GroqClient, "gemini": GeminiClient}


def get_client(provider: str):
    if provider == "fake":
        return FakeProvider()
    cls = _CLIENTS.get(provider)
    if cls is None:
        raise ProviderError(f"unknown provider: {provider}")
    return cls()


@dataclass
class FakeProvider:
    """Scripted offline provider.

    Replies are chosen by the [PHASE: ...] tag the protocol embeds in
    every prompt, and by the [LENS: ...] tag for debater identity, so a
    full debate runs deterministically with no network. `script` may
    override any reply: keys are (phase, lens) or (phase,).
    """

    name = "fake"
    script: dict = field(default_factory=dict)
    fail_with: Exception | None = None
    fail_models: frozenset = frozenset()

    def chat(self, system: str, user: str, model: str,
             max_tokens: int = 1600, temperature: float = 0.4) -> ChatResult:
        if model in self.fail_models:
            raise ProviderError(f"fake: model {model} configured to fail")
        if self.fail_with is not None:
            raise self.fail_with
        phase = _tag(user, "PHASE") or "unknown"
        lens = _tag(user, "LENS") or ""
        key = (phase, lens)
        if key in self.script:
            text = self.script[key]
        elif (phase,) in self.script:
            text = self.script[(phase,)]
        else:
            text = _default_fake_reply(phase, lens)
        return ChatResult(text=text, model_served=model,
                          prompt_tokens=100, completion_tokens=50,
                          latency_s=0.01)


def _tag(text: str, name: str) -> str | None:
    marker = f"[{name}:"
    i = text.find(marker)
    if i < 0:
        return None
    j = text.find("]", i)
    return text[i + len(marker):j].strip()


def _default_fake_reply(phase: str, lens: str) -> str:
    if phase == "baseline":
        return json.dumps({
            "choice": "A",
            "rationale": "Fake baseline: option A on the brief alone."})
    if phase == "opening":
        if lens.startswith("Risk"):
            return json.dumps({
                "choice": "B", "ranking": ["B", "A"],
                "argument": "Fake risk-lens opening for B.",
                "evidence": [{"claim": "fake claim", "cite": ["D1"]}],
                "risks": ["fake risk"], "key_assumption": "fake assumption",
                "confidence": "medium"})
        return json.dumps({
            "choice": "A", "ranking": ["A", "B"],
            "argument": "Fake outcomes-lens opening for A.",
            "evidence": [{"claim": "fake claim", "cite": ["D1"]}],
            "risks": ["fake risk"], "key_assumption": "fake assumption",
            "confidence": "medium"})
    if phase == "crossexam":
        return json.dumps({
            "strongest_opposing": {"debater": "peer",
                                   "argument_summary": "fake summary"},
            "attack": "Fake attack on the opposing argument.",
            "concessions": ["fake concession"],
            "would_change_my_mind": "Fake falsifier: a measured result X.",
            "questions_for_others": ["fake question"]})
    if phase == "revision":
        if lens.startswith("Risk"):
            return json.dumps({
                "choice": "B", "changed": False,
                "what_changed_it": "",
                "final_argument": "Fake final argument for B.",
                "residual_risks": ["fake residual risk"]})
        return json.dumps({
            "choice": "A", "changed": False,
            "what_changed_it": "",
            "final_argument": "Fake final argument for A.",
            "residual_risks": ["fake residual risk"]})
    if phase == "judge":
        return json.dumps({
            "decision": "A",
            "rationale": "Fake judge rationale relying on the fake attack.",
            "cross_examination_relied_on": [
                {"by": "peer", "point": "Fake attack on the opposing argument."}],
            "dissent": {"debater": "risk-lens debater",
                        "quote": "Fake final argument for B."},
            "confidence": {"level": "medium", "why": "fake"},
            "overturn_conditions": ["Fake overturn condition."],
            "unresolved_questions": ["fake open question"]})
    return "{}"
