"""Read-only, repository-scoped methods suitable for an LLM tool loop."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from refinery_ai.assessment.models import AnalyzerResult, Assessment, Finding
from refinery_ai.assessment.storage import AssessmentStore
from refinery_ai.context_graph.models import ContextEdge, ContextGraph, ContextNode
from refinery_ai.context_graph.store import ContextGraphError, ContextGraphStore
from refinery_ai.discovery import discover
from refinery_ai.models import DiscoverySnapshot
from refinery_ai.tools.models import (
    ContextNeighborhoodResult,
    ContextSearchResult,
    FindingSearchResult,
    InspectorResult,
    InspectorSummary,
    JsonObject,
    RepositoryOverview,
    SemanticRelationship,
    SemanticRelationshipResult,
    SymbolExplanation,
    SymbolListResult,
    SymbolSearchResult,
)

DEFAULT_GRAPH_PATH = Path(".refinery/context/context-graph.json")
MAX_FINDINGS = 100
MAX_CONTEXT_ITEMS = 200
MAX_NEIGHBORHOOD_DEPTH = 3
CALL_MARKERS = ("call", "invoke")
DEPENDENCY_TYPES = {"code", "injection", "database", "messaging", "cache", "configuration"}
DependencyType = Literal[
    "code", "injection", "database", "messaging", "cache", "configuration"
]


def _dump(value: BaseModel) -> JsonObject:
    return value.model_dump(mode="json")


def _summary(result: AnalyzerResult) -> InspectorSummary:
    return InspectorSummary(
        inspector=result.analyzer,
        status=result.status.value,
        completeness=result.completeness,
        metric_names=sorted(result.metrics),
        finding_count=len(result.findings),
        diagnostic_count=len(result.diagnostics),
    )


def _bounded_limit(value: int, maximum: int) -> int:
    if value < 1:
        raise ValueError("limit must be at least 1")
    return min(value, maximum)


def _matches_query(value: Any, query: str) -> bool:
    terms = [term.casefold() for term in query.split() if term]
    if not terms:
        return True
    text = json.dumps(value, default=str).casefold()
    return all(term in text for term in terms)


class RepositoryEvidenceTools:
    """Expose bounded persisted evidence without allowing model-triggered collection."""

    def __init__(
        self,
        repository: Path,
        *,
        assessment_store: AssessmentStore | None = None,
        graph_store: ContextGraphStore | None = None,
        graph_path: Path | None = None,
        snapshot: DiscoverySnapshot | None = None,
    ) -> None:
        self.repository = repository.expanduser().resolve()
        if not self.repository.is_dir():
            raise ValueError(f"Repository does not exist: {self.repository}")
        self.assessment_store = assessment_store or AssessmentStore(self.repository)
        self.graph_store = graph_store or ContextGraphStore()
        self.graph_path = graph_path or self.repository / DEFAULT_GRAPH_PATH
        self._snapshot = snapshot

    def tools(self) -> tuple[Callable[..., JsonObject], ...]:
        """Return semantic evidence methods to pass directly to ``run_tool_loop``."""

        return (
            self.get_repository_overview,
            self.list_symbols,
            self.find_symbol,
            self.explain_symbol,
            self.get_callers,
            self.get_callees,
            self.get_dependencies,
            self.get_dependents,
            self.search_findings,
            self.get_inspector_result,
        )

    def list_symbols(
        self,
        kind: Annotated[
            str | None,
            Field(
                description=(
                    "Optional exact symbol kind from available_kinds, such as spring.bean, "
                    "code.method, or http.endpoint"
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum symbols to return; values above 200 are capped")
        ] = 50,
    ) -> JsonObject:
        """List available symbols and kind counts when no symbol name is known yet."""

        selected_limit = _bounded_limit(limit, MAX_CONTEXT_ITEMS)
        graph = self._graph()
        if graph is None:
            return _dump(
                SymbolListResult(
                    available=False,
                    reason="No readable context graph is available",
                    kind=kind,
                )
            )
        available_kinds: dict[str, int] = {}
        for node in graph.nodes:
            available_kinds[node.kind] = available_kinds.get(node.kind, 0) + 1
        selected_kind = kind.casefold() if kind else None
        matched = [
            node
            for node in graph.nodes
            if selected_kind is None or node.kind.casefold() == selected_kind
        ]
        matched.sort(key=lambda node: (node.kind.casefold(), node.name.casefold(), node.id))
        reason = None
        if kind is not None and not matched:
            reason = f"No symbols have kind: {kind}. Choose a kind from available_kinds."
        return _dump(
            SymbolListResult(
                available=True,
                reason=reason,
                kind=kind,
                available_kinds=dict(sorted(available_kinds.items())),
                graph_created_at=graph.created_at,
                repository_fingerprint=graph.repository_fingerprint,
                matched_count=len(matched),
                returned_count=min(len(matched), selected_limit),
                truncated=len(matched) > selected_limit,
                symbols=matched[:selected_limit],
            )
        )

    def _discovery(self) -> DiscoverySnapshot:
        if self._snapshot is None:
            self._snapshot = discover(self.repository)
        return self._snapshot

    def _assessment(self) -> Assessment | None:
        assessments = self.assessment_store.list()
        return assessments[0] if assessments else None

    def _graph(self) -> ContextGraph | None:
        if not self.graph_path.is_file():
            return None
        try:
            graph = self.graph_store.load(self.graph_path)
        except ContextGraphError:
            return None
        if graph.repository.expanduser().resolve() != self.repository:
            return None
        return graph

    def get_repository_overview(self) -> JsonObject:
        """Return build, platform, assessment, inspector, and context-graph summary."""

        snapshot = self._discovery()
        assessment = self._assessment()
        graph = self._graph()
        return _dump(
            RepositoryOverview(
                repository=str(self.repository),
                build_system=snapshot.build_system.value,
                java_version=snapshot.java_version,
                frameworks=snapshot.frameworks,
                modules=[str(module.path) for module in snapshot.modules],
                declared_dependency_count=len(snapshot.dependencies),
                git_branch=snapshot.git_branch,
                dirty=snapshot.dirty,
                assessment_id=assessment.assessment_id if assessment else None,
                assessment_created_at=assessment.created_at if assessment else None,
                assessment_completeness=assessment.completeness if assessment else None,
                assessment_score=assessment.score.overall if assessment else None,
                inspectors=(
                    [_summary(result) for _, result in sorted(assessment.analyzers.items())]
                    if assessment
                    else []
                ),
                graph_available=graph is not None,
                graph_created_at=graph.created_at if graph else None,
                graph_node_count=len(graph.nodes) if graph else 0,
                graph_edge_count=len(graph.edges) if graph else 0,
                graph_providers=graph.provider_runs if graph else [],
                graph_fingerprint=graph.repository_fingerprint if graph else None,
            )
        )

    def list_inspector_results(self) -> JsonObject:
        """List persisted inspector status, completeness, metrics, and finding counts."""

        assessment = self._assessment()
        if assessment is None:
            return {
                "available": False,
                "reason": "No persisted assessment is available",
                "assessment_id": None,
                "inspectors": [],
            }
        return {
            "available": True,
            "reason": None,
            "assessment_id": assessment.assessment_id,
            "inspectors": [
                _summary(result).model_dump(mode="json")
                for _, result in sorted(assessment.analyzers.items())
            ],
        }

    def get_inspector_result(
        self,
        inspector: str,
        include_findings: bool = False,
        finding_limit: int = 20,
    ) -> JsonObject:
        """Return one persisted inspector result with optionally bounded findings."""

        limit = _bounded_limit(finding_limit, MAX_FINDINGS)
        assessment = self._assessment()
        result = assessment.analyzers.get(inspector) if assessment else None
        if result is None:
            return _dump(
                InspectorResult(
                    available=False,
                    reason=f"No persisted result is available for inspector: {inspector}",
                    inspector=inspector,
                    assessment_id=assessment.assessment_id if assessment else None,
                )
            )
        assert assessment is not None
        total = len(result.findings)
        visible = result.findings[:limit] if include_findings else []
        bounded = result.model_copy(update={"findings": visible})
        return _dump(
            InspectorResult(
                inspector=inspector,
                assessment_id=assessment.assessment_id,
                result=bounded,
                findings_truncated=include_findings and total > limit,
                total_findings=total,
                returned_findings=len(visible),
            )
        )

    def search_findings(
        self,
        query: str = "",
        inspector: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        path: str | None = None,
        limit: int = 20,
    ) -> JsonObject:
        """Search persisted findings by text, inspector, category, severity, and path."""

        selected_limit = _bounded_limit(limit, MAX_FINDINGS)
        assessment = self._assessment()
        if assessment is None:
            return _dump(
                FindingSearchResult(
                    available=False, reason="No persisted assessment is available"
                )
            )

        def matches(finding: Finding) -> bool:
            return (
                (inspector is None or finding.analyzer.casefold() == inspector.casefold())
                and (category is None or finding.category.value.casefold() == category.casefold())
                and (severity is None or finding.severity.casefold() == severity.casefold())
                and (path is None or path.casefold() in (finding.path or "").casefold())
                and _matches_query(finding.model_dump(mode="json"), query)
            )

        matched = sorted(
            (finding for finding in assessment.findings if matches(finding)),
            key=lambda item: (item.analyzer, item.path or "", item.line or 0, item.fingerprint),
        )
        return _dump(
            FindingSearchResult(
                assessment_id=assessment.assessment_id,
                matched_count=len(matched),
                returned_count=min(len(matched), selected_limit),
                truncated=len(matched) > selected_limit,
                findings=matched[:selected_limit],
            )
        )

    def search_context(
        self,
        query: str,
        node_kinds: list[str] | None = None,
        edge_kinds: list[str] | None = None,
        limit: int = 20,
    ) -> JsonObject:
        """Search graph nodes and relationships by text and optional exact kind filters."""

        selected_limit = _bounded_limit(limit, MAX_CONTEXT_ITEMS)
        graph = self._graph()
        if graph is None:
            return _dump(
                ContextSearchResult(
                    available=False, reason="No readable context graph is available"
                )
            )
        node_filter = {kind.casefold() for kind in node_kinds or []}
        edge_filter = {kind.casefold() for kind in edge_kinds or []}
        nodes = [
            node
            for node in graph.nodes
            if (not node_filter or node.kind.casefold() in node_filter)
            and _matches_query(node.model_dump(mode="json"), query)
        ]
        matched_node_ids = {node.id for node in nodes}
        edges = [
            edge
            for edge in graph.edges
            if (not edge_filter or edge.kind.casefold() in edge_filter)
            and (
                _matches_query(edge.model_dump(mode="json"), query)
                or edge.source in matched_node_ids
                or edge.target in matched_node_ids
            )
        ]
        return _dump(
            ContextSearchResult(
                graph_created_at=graph.created_at,
                repository_fingerprint=graph.repository_fingerprint,
                matched_node_count=len(nodes),
                returned_node_count=min(len(nodes), selected_limit),
                matched_edge_count=len(edges),
                returned_edge_count=min(len(edges), selected_limit),
                truncated=len(nodes) > selected_limit or len(edges) > selected_limit,
                nodes=nodes[:selected_limit],
                edges=edges[:selected_limit],
            )
        )

    def get_context_neighborhood(
        self,
        node_id: str,
        direction: Literal["incoming", "outgoing", "both"] = "both",
        edge_kinds: list[str] | None = None,
        depth: int = 1,
        limit: int = 50,
    ) -> JsonObject:
        """Return a bounded graph neighborhood around one exact context node ID."""

        if depth < 1 or depth > MAX_NEIGHBORHOOD_DEPTH:
            raise ValueError(f"depth must be between 1 and {MAX_NEIGHBORHOOD_DEPTH}")
        selected_limit = _bounded_limit(limit, MAX_CONTEXT_ITEMS)
        graph = self._graph()
        if graph is None:
            return _dump(
                ContextNeighborhoodResult(
                    available=False,
                    reason="No readable context graph is available",
                    node_id=node_id,
                    depth=depth,
                )
            )
        node_by_id = {node.id: node for node in graph.nodes}
        if node_id not in node_by_id:
            return _dump(
                ContextNeighborhoodResult(
                    available=False,
                    reason=f"Context node was not found: {node_id}",
                    node_id=node_id,
                    graph_created_at=graph.created_at,
                    repository_fingerprint=graph.repository_fingerprint,
                    depth=depth,
                )
            )
        kinds = {kind.casefold() for kind in edge_kinds or []}
        eligible = [edge for edge in graph.edges if not kinds or edge.kind.casefold() in kinds]
        visited = {node_id}
        frontier = {node_id}
        selected_edges: dict[tuple[str, str, str], ContextEdge] = {}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in eligible:
                incoming = direction in ("incoming", "both") and edge.target in frontier
                outgoing = direction in ("outgoing", "both") and edge.source in frontier
                if not incoming and not outgoing:
                    continue
                selected_edges[(edge.source, edge.target, edge.kind)] = edge
                if incoming:
                    next_frontier.add(edge.source)
                if outgoing:
                    next_frontier.add(edge.target)
            next_frontier -= visited
            if not next_frontier:
                break
            visited.update(next_frontier)
            frontier = next_frontier
        nodes = [node for node in graph.nodes if node.id in visited]
        edges = list(selected_edges.values())
        return _dump(
            ContextNeighborhoodResult(
                node_id=node_id,
                graph_created_at=graph.created_at,
                repository_fingerprint=graph.repository_fingerprint,
                depth=depth,
                matched_node_count=len(nodes),
                returned_node_count=min(len(nodes), selected_limit),
                matched_edge_count=len(edges),
                returned_edge_count=min(len(edges), selected_limit),
                truncated=len(nodes) > selected_limit or len(edges) > selected_limit,
                nodes=nodes[:selected_limit],
                edges=edges[:selected_limit],
            )
        )

    def find_symbol(
        self,
        query: Annotated[
            str,
            Field(
                description=(
                    "A simple or qualified symbol name, such as OrderService or "
                    "example.OrderService"
                )
            ),
        ],
        kind: Annotated[
            str | None,
            Field(
                description=(
                    "Optional exact graph kind, such as spring.bean, code.method, or "
                    "http.endpoint"
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum symbols to return; values above 200 are capped")
        ] = 20,
    ) -> JsonObject:
        """Find classes, methods, fields, endpoints, beans, or other named symbols."""

        selected_limit = _bounded_limit(limit, MAX_CONTEXT_ITEMS)
        graph = self._graph()
        if graph is None:
            return _dump(
                SymbolSearchResult(
                    available=False, reason="No readable context graph is available"
                )
            )
        selected_kind = kind.casefold() if kind else None
        matched = [
            node
            for node in graph.nodes
            if (selected_kind is None or node.kind.casefold() == selected_kind)
            and _matches_query(node.model_dump(mode="json"), query)
        ]
        normalized = query.casefold()
        matched.sort(
            key=lambda node: (
                0
                if node.name.casefold() == normalized or node.id.casefold() == normalized
                else 1
                if node.name.casefold().startswith(normalized)
                else 2,
                node.name.casefold(),
                node.id,
            )
        )
        return _dump(
            SymbolSearchResult(
                graph_created_at=graph.created_at,
                repository_fingerprint=graph.repository_fingerprint,
                matched_count=len(matched),
                returned_count=min(len(matched), selected_limit),
                truncated=len(matched) > selected_limit,
                symbols=matched[:selected_limit],
            )
        )

    def explain_symbol(
        self,
        symbol_id: Annotated[
            str, Field(description="Exact symbol ID returned by find_symbol")
        ],
        limit: Annotated[
            int,
            Field(
                description="Maximum immediate relationships to return; values above 200 are capped"
            ),
        ] = 50,
    ) -> JsonObject:
        """Describe a symbol, its source evidence, and its immediate relationships."""

        selected_limit = _bounded_limit(limit, MAX_CONTEXT_ITEMS)
        graph = self._graph()
        if graph is None:
            return _dump(
                SymbolExplanation(
                    available=False,
                    reason="No readable context graph is available",
                    symbol_id=symbol_id,
                )
            )
        node_by_id = {node.id: node for node in graph.nodes}
        symbol = node_by_id.get(symbol_id)
        if symbol is None:
            return _dump(
                SymbolExplanation(
                    available=False,
                    reason=f"Symbol was not found: {symbol_id}. Use find_symbol first.",
                    symbol_id=symbol_id,
                    graph_created_at=graph.created_at,
                    repository_fingerprint=graph.repository_fingerprint,
                )
            )
        incoming = [edge for edge in graph.edges if edge.target == symbol_id]
        outgoing = [edge for edge in graph.edges if edge.source == symbol_id]
        edges = [*incoming, *outgoing]
        visible_edges = edges[:selected_limit]
        related_ids = {
            edge.source if edge.target == symbol_id else edge.target
            for edge in visible_edges
        }
        return _dump(
            SymbolExplanation(
                symbol_id=symbol_id,
                graph_created_at=graph.created_at,
                repository_fingerprint=graph.repository_fingerprint,
                symbol=symbol,
                incoming=[edge for edge in visible_edges if edge.target == symbol_id],
                outgoing=[edge for edge in visible_edges if edge.source == symbol_id],
                related_symbols=[node for node in graph.nodes if node.id in related_ids],
                truncated=len(edges) > selected_limit,
            )
        )

    def get_callers(
        self,
        symbol_id: Annotated[
            str, Field(description="Exact symbol ID returned by find_symbol")
        ],
        depth: Annotated[
            int, Field(description="Number of caller hops to follow, from 1 through 3")
        ] = 1,
        limit: Annotated[
            int, Field(description="Maximum relationships to return; values above 200 are capped")
        ] = 50,
    ) -> JsonObject:
        """Find symbols that call or invoke a symbol, with explicit call-evidence coverage."""

        return self._semantic_relationships(
            symbol_id, relationship="callers", direction="incoming", depth=depth, limit=limit
        )

    def get_callees(
        self,
        symbol_id: Annotated[
            str, Field(description="Exact symbol ID returned by find_symbol")
        ],
        depth: Annotated[
            int, Field(description="Number of call hops to follow, from 1 through 3")
        ] = 1,
        limit: Annotated[
            int, Field(description="Maximum relationships to return; values above 200 are capped")
        ] = 50,
    ) -> JsonObject:
        """Find symbols called or invoked by a symbol, with explicit call-evidence coverage."""

        return self._semantic_relationships(
            symbol_id, relationship="callees", direction="outgoing", depth=depth, limit=limit
        )

    def get_dependencies(
        self,
        symbol_id: Annotated[
            str, Field(description="Exact symbol ID returned by find_symbol")
        ],
        dependency_types: Annotated[
            list[DependencyType] | None,
            Field(
                description=(
                    "Optional filters: code, injection, database, messaging, cache, "
                    "configuration"
                )
            ),
        ] = None,
        depth: Annotated[
            int, Field(description="Number of dependency hops to follow, from 1 through 3")
        ] = 1,
        limit: Annotated[
            int, Field(description="Maximum relationships to return; values above 200 are capped")
        ] = 50,
    ) -> JsonObject:
        """Find code and infrastructure resources that a symbol depends on."""

        return self._semantic_relationships(
            symbol_id,
            relationship="dependencies",
            direction="outgoing",
            depth=depth,
            limit=limit,
            dependency_types=dependency_types,
        )

    def get_dependents(
        self,
        symbol_id: Annotated[
            str, Field(description="Exact symbol ID returned by find_symbol")
        ],
        dependency_types: Annotated[
            list[DependencyType] | None,
            Field(
                description=(
                    "Optional filters: code, injection, database, messaging, cache, "
                    "configuration"
                )
            ),
        ] = None,
        depth: Annotated[
            int, Field(description="Number of dependent hops to follow, from 1 through 3")
        ] = 1,
        limit: Annotated[
            int, Field(description="Maximum relationships to return; values above 200 are capped")
        ] = 50,
    ) -> JsonObject:
        """Find code, endpoints, or resources that depend on a symbol."""

        return self._semantic_relationships(
            symbol_id,
            relationship="dependents",
            direction="incoming",
            depth=depth,
            limit=limit,
            dependency_types=dependency_types,
        )

    def _semantic_relationships(
        self,
        symbol_id: str,
        *,
        relationship: str,
        direction: Literal["incoming", "outgoing"],
        depth: int,
        limit: int,
        dependency_types: Sequence[str] | None = None,
    ) -> JsonObject:
        if depth < 1 or depth > MAX_NEIGHBORHOOD_DEPTH:
            raise ValueError(f"depth must be between 1 and {MAX_NEIGHBORHOOD_DEPTH}")
        selected_limit = _bounded_limit(limit, MAX_CONTEXT_ITEMS)
        requested_types = {item.casefold() for item in dependency_types or []}
        unknown_types = requested_types - DEPENDENCY_TYPES
        if unknown_types:
            raise ValueError(f"Unknown dependency types: {', '.join(sorted(unknown_types))}")
        graph = self._graph()
        if graph is None:
            return _dump(
                SemanticRelationshipResult(
                    available=False,
                    reason="No readable context graph is available",
                    symbol_id=symbol_id,
                    relationship=relationship,
                )
            )
        node_by_id = {node.id: node for node in graph.nodes}
        if symbol_id not in node_by_id:
            return _dump(
                SemanticRelationshipResult(
                    available=False,
                    reason=f"Symbol was not found: {symbol_id}. Use find_symbol first.",
                    symbol_id=symbol_id,
                    relationship=relationship,
                    graph_created_at=graph.created_at,
                    repository_fingerprint=graph.repository_fingerprint,
                )
            )
        calls_only = relationship in {"callers", "callees"}
        call_edges = [edge for edge in graph.edges if self._is_call(edge)]
        eligible = call_edges if calls_only else graph.edges
        visited = {symbol_id}
        frontier = {symbol_id}
        results: list[SemanticRelationship] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in eligible:
                if direction == "incoming" and edge.target in frontier:
                    related_id = edge.source
                elif direction == "outgoing" and edge.source in frontier:
                    related_id = edge.target
                else:
                    continue
                related = node_by_id.get(related_id)
                if related is None:
                    continue
                anchor_id = edge.target if direction == "incoming" else edge.source
                relationship_type = self._dependency_type(edge, related, node_by_id.get(anchor_id))
                key = (edge.source, edge.target, edge.kind)
                if key not in seen_edges and (
                    not requested_types or relationship_type in requested_types
                ):
                    results.append(
                        SemanticRelationship(
                            relationship_type=relationship_type,
                            direction=direction,
                            symbol=related,
                            relationship=edge,
                        )
                    )
                    seen_edges.add(key)
                if related_id not in visited:
                    next_frontier.add(related_id)
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        coverage = "call-level" if call_edges else "relationship-level"
        warning = None
        if calls_only and not call_edges:
            warning = (
                "The graph contains no call or invocation relationships. An empty result does "
                "not prove that the symbol has no callers or callees."
            )
        return _dump(
            SemanticRelationshipResult(
                symbol_id=symbol_id,
                relationship=relationship,
                coverage=coverage,
                warning=warning,
                graph_created_at=graph.created_at,
                repository_fingerprint=graph.repository_fingerprint,
                matched_count=len(results),
                returned_count=min(len(results), selected_limit),
                truncated=len(results) > selected_limit,
                results=results[:selected_limit],
            )
        )

    @staticmethod
    def _is_call(edge: ContextEdge) -> bool:
        kind = edge.kind.casefold()
        return any(marker in kind for marker in CALL_MARKERS)

    @staticmethod
    def _dependency_type(
        edge: ContextEdge, related: ContextNode, anchor: ContextNode | None
    ) -> str:
        kinds = {related.kind.casefold(), anchor.kind.casefold() if anchor else ""}
        edge_kind = edge.kind.casefold()
        if "inject" in edge_kind:
            return "injection"
        if any(kind.startswith("database.") for kind in kinds):
            return "database"
        if any(kind.startswith("kafka.") for kind in kinds):
            return "messaging"
        if any(kind.startswith("redis.") for kind in kinds):
            return "cache"
        if any(kind.startswith("configuration.") for kind in kinds) or "config" in edge_kind:
            return "configuration"
        return "code"
