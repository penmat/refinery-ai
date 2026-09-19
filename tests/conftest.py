import pytest

from refinery_ai.context_graph.contracts import ContextFragment, ContextRequest
from refinery_ai.context_graph.providers.codegraph import CodeGraphProvider


@pytest.fixture(autouse=True)
def disable_telemetry_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests isolated from developer and CI OpenTelemetry configuration."""

    monkeypatch.setenv("REFINERY_OTEL_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-langsmith-key")


@pytest.fixture
def stub_codegraph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI tests independent of the external CodeGraph installation."""

    def collect(self: CodeGraphProvider, request: ContextRequest) -> ContextFragment:
        return ContextFragment(provider="codegraph", diagnostics=["stub CodeGraph index"])

    monkeypatch.setattr(CodeGraphProvider, "collect", collect)
