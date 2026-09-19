import os

from refinery_ai.config import Settings
from refinery_ai.telemetry import (
    _headers,
    _signal_endpoint,
    configure_langsmith,
    configure_telemetry,
    observe_llm,
    record_llm_usage,
)


def test_langsmith_settings_from_dotenv_are_exported(tmp_path, monkeypatch) -> None:
    tmp_path.joinpath(".env").write_text(
        "LANGSMITH_API_KEY=test-langsmith-key\n"
        "LANGSMITH_TRACING=true\n"
        "LANGSMITH_PROJECT=refinery-test\n"
        "LANGSMITH_ENDPOINT=https://langsmith.example.test\n"
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "LANGSMITH_API_KEY",
        "LANGSMITH_TRACING",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)

    active = configure_langsmith(Settings())

    assert active is True
    assert os.environ["LANGSMITH_API_KEY"] == "test-langsmith-key"
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "refinery-test"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://langsmith.example.test"


def test_telemetry_can_be_disabled() -> None:
    assert configure_telemetry(Settings(otel_enabled=False)) is False


def test_signal_endpoint_accepts_base_or_signal_url() -> None:
    assert _signal_endpoint("https://otel.example/api/public/otel", "traces") == (
        "https://otel.example/api/public/otel/v1/traces"
    )
    assert _signal_endpoint("http://collector:4318/v1/traces", "metrics") == (
        "http://collector:4318/v1/metrics"
    )


def test_otel_headers_are_parsed_and_url_decoded() -> None:
    assert _headers("Authorization=Basic%20abc%3D,x-team=refinery") == {
        "Authorization": "Basic abc=",
        "x-team": "refinery",
    }


def test_usage_records_workflow_labels(monkeypatch) -> None:
    class Counter:
        def __init__(self) -> None:
            self.values = []

        def add(self, amount, attributes) -> None:
            self.values.append((amount, attributes))

    tokens = Counter()
    requests = Counter()
    monkeypatch.setattr("refinery_ai.telemetry._token_counter", tokens)
    monkeypatch.setattr("refinery_ai.telemetry._request_counter", requests)

    with observe_llm("repair", run_id="run-1", plan_id="plan-1", repair_attempt=2):
        record_llm_usage(
            {"prompt_tokens": 100, "completion_tokens": 25, "requests": 1},
            model="test-model",
        )

    assert [value[0] for value in tokens.values] == [100, 25]
    assert tokens.values[0][1]["refinery.phase"] == "repair"
    assert tokens.values[0][1]["refinery.run.id"] == "run-1"
    assert tokens.values[0][1]["refinery.plan.id"] == "plan-1"
    assert tokens.values[0][1]["refinery.repair.attempt"] == 2
    assert requests.values[0][0] == 1
