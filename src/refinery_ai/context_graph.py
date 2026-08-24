"""Provider-neutral repository context graph collection and querying."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from pydantic import JsonValue

from refinery_ai.models import (
    ContextEdge,
    ContextGraph,
    ContextGraphInfo,
    ContextNode,
    DiscoverySnapshot,
    ProviderRun,
)


class ContextGraphError(RuntimeError):
    """Raised when required context cannot be collected or persisted."""


@dataclass(frozen=True)
class ContextRequest:
    repository: Path
    discovery: DiscoverySnapshot | None = None


@dataclass
class ContextFragment:
    provider: str
    nodes: list[ContextNode] = field(default_factory=list)
    edges: list[ContextEdge] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


class ContextProvider(Protocol):
    @property
    def name(self) -> str: ...

    def supports(self, discovery: DiscoverySnapshot | None) -> bool: ...

    def collect(self, request: ContextRequest) -> ContextFragment: ...


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


class ContextGraphBuilder:
    """Collect, merge, and persist context from independently useful providers."""

    def __init__(
        self,
        providers: list[ContextProvider],
        *,
        store: ContextGraphStore | None = None,
        optional_providers: set[str] | None = None,
    ) -> None:
        self.providers = providers
        self.store = store or ContextGraphStore()
        self.optional_providers = optional_providers or set()

    def build(
        self, repository: Path, discovery: DiscoverySnapshot | None = None
    ) -> ContextGraphInfo:
        root = repository.expanduser().resolve()
        nodes: dict[str, ContextNode] = {}
        edges: dict[tuple[str, str, str], ContextEdge] = {}
        runs: list[ProviderRun] = []
        request = ContextRequest(repository=root, discovery=discovery)
        for provider in self.providers:
            if not provider.supports(discovery):
                runs.append(ProviderRun(provider=provider.name, status="skipped"))
                continue
            try:
                fragment = provider.collect(request)
            except Exception as error:
                if provider.name not in self.optional_providers:
                    raise
                runs.append(
                    ProviderRun(
                        provider=provider.name,
                        status="failed",
                        diagnostics=[str(error)],
                    )
                )
                continue
            for node in fragment.nodes:
                existing = nodes.get(node.id)
                nodes[node.id] = node if existing is None else self._merge_node(existing, node)
            for edge in fragment.edges:
                key = (edge.source, edge.target, edge.kind)
                existing_edge = edges.get(key)
                edges[key] = (
                    edge if existing_edge is None else self._merge_edge(existing_edge, edge)
                )
            runs.append(
                ProviderRun(
                    provider=fragment.provider,
                    node_count=len(fragment.nodes),
                    edge_count=len(fragment.edges),
                    diagnostics=fragment.diagnostics,
                )
            )
        graph = ContextGraph(
            repository=root,
            repository_fingerprint=repository_fingerprint(root),
            nodes=sorted(nodes.values(), key=lambda item: item.id),
            edges=sorted(edges.values(), key=lambda item: (item.source, item.target, item.kind)),
            provider_runs=runs,
        )
        return self.store.save(graph)

    @staticmethod
    def _merge_node(left: ContextNode, right: ContextNode) -> ContextNode:
        return left.model_copy(
            update={
                "attributes": ContextGraphBuilder._merge_attributes(
                    left.attributes, right.attributes
                ),
                "sources": [*left.sources, *right.sources],
            }
        )

    @staticmethod
    def _merge_edge(left: ContextEdge, right: ContextEdge) -> ContextEdge:
        return left.model_copy(
            update={
                "attributes": ContextGraphBuilder._merge_attributes(
                    left.attributes, right.attributes
                ),
                "sources": [*left.sources, *right.sources],
            }
        )

    @staticmethod
    def _merge_attributes(
        left: Mapping[str, JsonValue], right: Mapping[str, JsonValue]
    ) -> dict[str, JsonValue]:
        merged = dict(left)
        for key, value in right.items():
            if key not in merged:
                merged[key] = value
            elif merged[key] != value:
                existing = merged[key]
                values = existing if isinstance(existing, list) else [existing]
                combined = values if value in values else [*values, value]
                merged[key] = cast(JsonValue, combined)
        return merged


class ContextGraphQuery:
    """Deterministic bounded retrieval over the normalized context graph."""

    def __init__(self, store: ContextGraphStore | None = None, *, limit: int = 50) -> None:
        self.store = store or ContextGraphStore()
        self.limit = limit

    def query(self, graph_path: Path, question: str) -> str:
        graph = self.store.load(graph_path)
        terms = {term.casefold() for term in question.split() if len(term) > 2}

        def matches(value: object) -> bool:
            text = json.dumps(value, default=str).casefold()
            return not terms or any(term in text for term in terms)

        nodes = [node for node in graph.nodes if matches(node.model_dump())][: self.limit]
        node_ids = {node.id for node in nodes}
        edges = [
            edge
            for edge in graph.edges
            if edge.source in node_ids or edge.target in node_ids or matches(edge.model_dump())
        ][: self.limit]
        return json.dumps(
            {
                "question": question,
                "nodes": [node.model_dump(mode="json") for node in nodes],
                "edges": [edge.model_dump(mode="json") for edge in edges],
            },
            indent=2,
        )


def repository_fingerprint(repository: Path) -> str:
    """Hash relevant repository inputs while ignoring generated state."""

    ignored = {".git", ".gradle", ".idea", ".refinery", "build", "target", "graphify-out"}
    relevant_names = {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    }
    relevant_suffixes = {".java", ".kt", ".xml", ".sql", ".properties", ".yml", ".yaml"}
    digest = hashlib.sha256()
    for path in sorted(repository.rglob("*")):
        if not path.is_file() or ignored.intersection(path.relative_to(repository).parts):
            continue
        if path.name not in relevant_names and path.suffix.casefold() not in relevant_suffixes:
            continue
        relative = path.relative_to(repository)
        digest.update(relative.as_posix().encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()
