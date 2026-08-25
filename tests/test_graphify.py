import json
import subprocess
from pathlib import Path

import pytest

from refinery_ai.context_graph.builder import ContextGraphBuilder
from refinery_ai.context_graph.contracts import ContextRequest
from refinery_ai.context_graph.providers.graphify import GraphifyError, GraphifyProvider


def completed(command: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def test_build_reads_graphify_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "graphify-out"
    output.mkdir()
    (output / "graph.json").write_text(
        json.dumps({"nodes": [{"id": "A"}, {"id": "B"}], "edges": [{"source": "A"}]})
    )

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed(["graphify"])

    monkeypatch.setattr(subprocess, "run", run)
    info = ContextGraphBuilder([GraphifyProvider()]).build(tmp_path)

    assert info.node_count == 2
    assert info.edge_count == 1
    assert info.provider == "context-graph"
    assert info.graph_path == tmp_path / ".refinery/context/context-graph.json"
    assert info.provider_runs[0].provider == "graphify"


def test_query_uses_explicit_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = tmp_path / "graphify-out" / "graph.json"
    graph.parent.mkdir()
    graph.touch()
    seen: list[str] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.extend(command)
        return completed(command, "scoped result\n")

    monkeypatch.setattr(subprocess, "run", run)
    result = GraphifyProvider().query(graph, "show service dependencies")

    assert result == "scoped result"
    assert seen[-5:] == [
        "graphify",
        "query",
        "show service dependencies",
        "--graph",
        str(graph),
    ]


def test_missing_graphify_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(GraphifyError, match="uv sync"):
        GraphifyProvider().collect(ContextRequest(repository=tmp_path))
