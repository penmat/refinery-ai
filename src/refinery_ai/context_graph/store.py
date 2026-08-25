"""Persistence for normalized context graphs."""

from __future__ import annotations

from pathlib import Path

from refinery_ai.context_graph.models import ContextGraph, ContextGraphInfo


class ContextGraphError(RuntimeError):
    """Raised when required context cannot be collected or persisted."""


class ContextGraphStore:
    """Persist the normalized graph independently of provider-native artifacts."""

    def __init__(self, relative_path: Path = Path(".refinery/context/context-graph.json")) -> None:
        self.relative_path = relative_path

    def save(self, graph: ContextGraph) -> ContextGraphInfo:
        graph_path = graph.repository / self.relative_path
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(graph.model_dump_json(indent=2))
        return ContextGraphInfo(
            graph_path=graph_path,
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            schema_version=graph.schema_version,
            provider_runs=graph.provider_runs,
        )

    def load(self, graph_path: Path) -> ContextGraph:
        try:
            return ContextGraph.model_validate_json(graph_path.read_text())
        except (OSError, ValueError) as error:
            raise ContextGraphError(f"Unable to read context graph: {error}") from error
