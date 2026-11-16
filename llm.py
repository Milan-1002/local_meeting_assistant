"""One call shape for both providers, plus a running token/cost tally."""

from __future__ import annotations

import json
from collections import defaultdict

from config import CLAUDE_EFFORT, OPENAI_REASONING, PRICES

# Models that accept server-side refusal fallbacks ("default" routing).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

usage: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "cached": 0})
_clients: dict[str, object] = {}


def _anthropic():
    if "anthropic" not in _clients:
        import anthropic

        _clients["anthropic"] = anthropic.AsyncAnthropic()
    return _clients["anthropic"]


def _openai():
    if "openai" not in _clients:
        from openai import AsyncOpenAI

        _clients["openai"] = AsyncOpenAI()
    return _clients["openai"]


def usage_summary() -> dict:
    calls = sum(u["calls"] for u in usage.values())
    tokens = sum(u["in"] + u["out"] for u in usage.values())
    cost, unpriced, by_model = 0.0, 0, {}
    for model, u in usage.items():
        by_model[model] = dict(u)
        if model not in PRICES:
            unpriced += u["in"] + u["out"]
            continue
        i, c, o = PRICES[model]
        by_model[model]["cost"] = round(((u["in"] - u["cached"]) * i + u["cached"] * c + u["out"] * o) / 1e6, 4)
        cost += by_model[model]["cost"]
    return {"calls": calls, "tokens": tokens, "cost": round(cost, 4), "priced": unpriced == 0,
            "unpriced_tokens": unpriced, "by_model": by_model}


async def complete(model: str, system: str, user: str, *, schema: dict | None = None,
                   max_tokens: int = 4000, effort: str | None = None) -> str | dict:
    """Run one prompt. `system` should be the stable part (it is cached); put what changes in `user`.
    Returns parsed JSON when a schema is given, else text. `effort` overrides the default reasoning level."""
    if model.startswith("claude"):
        text = await _call_claude(model, system, user, schema, max_tokens, effort or CLAUDE_EFFORT)
    else:
        text = await _call_openai(model, system, user, schema, max_tokens, effort or OPENAI_REASONING)
    return json.loads(text) if schema else text


async def _call_claude(model, system, user, schema, max_tokens, effort) -> str:
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
    )
    if schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    client = _anthropic()
    if model in FALLBACK_MODELS:
        # If a safety classifier declines, the API reruns on a suitable fallback model.
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        response = await client.beta.messages.create(**kwargs)
    else:
        response = await client.messages.create(**kwargs)
    u = usage[model]
    cached = response.usage.cache_read_input_tokens or 0
    u["calls"] += 1
    u["in"] += response.usage.input_tokens + cached + (response.usage.cache_creation_input_tokens or 0)
    u["cached"] += cached
    u["out"] += response.usage.output_tokens
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request")
    return next(b.text for b in response.content if b.type == "text")


async def _call_openai(model, system, user, schema, max_tokens, effort) -> str:
    kwargs: dict = dict(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_completion_tokens=max_tokens,
    )
    if model.startswith(("gpt-5", "o")):
        kwargs["reasoning_effort"] = effort
    if schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "schema": schema, "strict": True},
        }
    response = await _openai().chat.completions.create(**kwargs)
    u = usage[model]
    u["calls"] += 1
    u["in"] += response.usage.prompt_tokens
    u["out"] += response.usage.completion_tokens
    details = getattr(response.usage, "prompt_tokens_details", None)
    u["cached"] += getattr(details, "cached_tokens", 0) or 0
    msg = response.choices[0].message
    if getattr(msg, "refusal", None):
        raise RuntimeError(f"model declined: {msg.refusal}")
    return msg.content or ""
