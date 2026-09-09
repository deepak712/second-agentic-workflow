"""Claude API helpers for tools. Separated from _lib.py so _lib stays dep-free."""
from __future__ import annotations

import json
import sys

import anthropic

import _lib

# Models used by this project (see CLAUDE.md decisions).
OPUS = "claude-opus-5"       # report synthesis + buy/wait theses
SONNET = "claude-sonnet-5"   # per-batch headline triage

# USD per 1M tokens: (input, output). Keep in sync with Anthropic pricing.
RATES = {
    OPUS: (5.0, 25.0),
    SONNET: (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_client = None


def client() -> "anthropic.Anthropic":
    global _client
    if _client is None:
        _lib.require_env("ANTHROPIC_API_KEY")
        _client = anthropic.Anthropic()
    return _client


def estimate(model: str, system: str, user: str, max_output_tokens: int = 4000) -> dict:
    """Token + rough USD estimate for one create() call. Used by dry-run gates."""
    in_rate, out_rate = RATES.get(model, RATES[OPUS])
    try:
        counted = client().messages.count_tokens(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        in_tokens = counted.input_tokens
    except Exception as exc:  # count_tokens failed - fall back to a chars/4 guess
        in_tokens = (len(system) + len(user)) // 4
        print(f"  (count_tokens unavailable: {exc}; estimating from length)", file=sys.stderr)
    est_usd = in_tokens / 1e6 * in_rate + max_output_tokens / 1e6 * out_rate
    return {"model": model, "input_tokens": in_tokens,
            "assumed_output_tokens": max_output_tokens, "est_usd": round(est_usd, 4)}


def print_estimate(est: dict) -> None:
    print(
        f"[dry run] {est['model']}: ~{est['input_tokens']:,} input tokens, "
        f"assuming <={est['assumed_output_tokens']:,} output -> ~${est['est_usd']:.3f}\n"
        f"Re-run with --confirm to spend.",
        file=sys.stderr,
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


def complete_json(model: str, system: str, user: str, max_tokens: int = 4000):
    """One messages.create call; parse the text response as JSON."""
    resp = client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system + "\n\nRespond with valid JSON only. No prose, no code fences.",
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(_strip_fences(text))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Model did not return valid JSON ({exc}).\n--- raw response ---\n{text}"
        )


def complete_text(model: str, system: str, user: str, max_tokens: int = 12000) -> str:
    """One call for prose output. Streams so large max_tokens doesn't time out,
    and warns if the response was truncated at the token cap."""
    with client().messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        resp = stream.get_final_message()
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if resp.stop_reason == "max_tokens":
        print(f"  WARNING: response hit the {max_tokens}-token cap and may be "
              f"truncated. Raise max_tokens or shorten the prompt.", file=sys.stderr)
    return text
