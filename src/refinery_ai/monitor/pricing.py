"""Standard text-token estimates, independent of telemetry export."""

import math

# USD per million tokens. LiteLLM installations can predate Refinery's default
# model. Source: https://developers.openai.com/api/docs/models/gpt-5.6-luna
_OPENAI_FALLBACK = {"gpt-5.6-luna": (0.20, 1.20)}


def estimate_cost(
    model: str | None, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    if not model or input_tokens is None or output_tokens is None:
        return None
    if min(input_tokens, output_tokens) < 0:
        return None

    # Preserve local model tags (e.g. llama:latest). Only translate the
    # provider prefixes Refinery uses in configuration.
    if model.startswith(("openai:", "ollama:")):
        model = model.replace(":", "/", 1)
    if model.startswith(("ollama/", "ollama_chat/")):
        return None

    from litellm import model_cost

    entry = model_cost.get(model)
    openai_name = model.removeprefix("openai/")
    if entry is None and model.startswith("openai/"):
        candidate = model_cost.get(openai_name)
        if candidate and candidate.get("litellm_provider") == "openai":
            entry = candidate
    if entry is not None:
        rates = (entry.get("input_cost_per_token"), entry.get("output_cost_per_token"))
        if all(
            isinstance(rate, (int, float)) and math.isfinite(rate) and rate >= 0 for rate in rates
        ):
            return input_tokens * float(rates[0] or 0) + output_tokens * float(rates[1] or 0)
    if "/" not in openai_name and openai_name in _OPENAI_FALLBACK:
        input_rate, output_rate = _OPENAI_FALLBACK[openai_name]
        return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return None
