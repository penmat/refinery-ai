"""Deterministic context-provider collection and graph merging."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from refinery_ai.context_graph.contracts import ContextProvider, ContextRequest
from refinery_ai.context_graph.fingerprint import repository_fingerprint
from refinery_ai.context_graph.models import (
    ContextEdge,
    ContextGraph,
    ContextGraphInfo,
    ContextNode,
    ProviderRun,
)
from refinery_ai.context_graph.store import ContextGraphStore
from refinery_ai.logging import get_logger
from refinery_ai.models import DiscoverySnapshot

logger = get_logger(__name__)


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
                logger.info("Running context provider: %s", provider.name)
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
            logger.info("Completed context provider: %s", provider.name)
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
