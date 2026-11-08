import pytest

from refinery_ai.monitor.pricing import estimate_cost


def test_standard_rates_and_default_model_fallback(monkeypatch):
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "example": {
                "litellm_provider": "openai",
                "input_cost_per_token": 2e-6,
                "output_cost_per_token": 4e-6,
            },
        },
    )
    assert estimate_cost("openai:example", 1000, 500) == pytest.approx(0.004)
    assert estimate_cost("openai:gpt-5.6-luna", 1000, 500) == pytest.approx(0.0008)
    assert estimate_cost("openai:example", 0, 0) == 0
    assert estimate_cost("ollama:gpt-5.6-luna", 1000, 500) is None
    assert estimate_cost("another/example", 1000, 500) is None
    assert estimate_cost("openai:example", 1000, None) is None


def test_explicitly_unpriced_usage_keeps_token_counts():
    from refinery_ai.monitor.recording import capture_usage
    from refinery_ai.telemetry import record_llm_usage

    records = []
    with capture_usage(lambda *usage: records.append(usage)):
        record_llm_usage(
            {"input_tokens": 100, "output_tokens": 25},
            model="gpt-5.6-luna",
            price_usage=False,
        )
    assert records == [(100, 25, "gpt-5.6-luna", None)]
