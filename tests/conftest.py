import pytest

from refinery_ai.context_graph.contracts import ContextFragment, ContextRequest
from refinery_ai.context_graph.providers.codegraph import CodeGraphProvider


@pytest.fixture
def stub_codegraph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI tests independent of the external CodeGraph installation."""

    def collect(self: CodeGraphProvider, request: ContextRequest) -> ContextFragment:
        return ContextFragment(provider="codegraph", diagnostics=["stub CodeGraph index"])

    monkeypatch.setattr(CodeGraphProvider, "collect", collect)
