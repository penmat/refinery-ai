"""Deterministic project architecture summary aggregation."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from refinery_ai.assessment.dependency_artifacts import write_dependency_artifact
from refinery_ai.assessment.engine import AssessmentEngine
from refinery_ai.assessment.models import Assessment
from refinery_ai.config import Settings
from refinery_ai.context_graph.fingerprint import repository_fingerprint
from refinery_ai.context_graph.models import ContextEdge, ContextGraph, ContextNode
from refinery_ai.context_graph.store import ContextGraphStore
from refinery_ai.discovery import discover
from refinery_ai.harness import HarnessRepositoryEnvironment
from refinery_ai.models import DiscoverySnapshot
from refinery_ai.summary.models import (
    ArchitectureSummary,
    DataArchitecture,
    EvidenceSummary,
    ExternalInterfaces,
    ModernizationPosture,
    ProjectOverview,
    ProjectStructure,
    ProjectSummary,
    ProminentComponent,
    SummaryObservation,
)

_IGNORED = {
    ".git",
    ".gradle",
    ".idea",
    ".refinery",
    ".codegraph",
    "build",
    "target",
    "graphify-out",
}


class SummaryError(RuntimeError):
    """Raised when a project summary cannot be produced from trustworthy inputs."""


class ProjectSummaryService:
    def __init__(self, store: ContextGraphStore | None = None) -> None:
        self.store = store or ContextGraphStore()

    def summarize(
        self,
        repository: Path,
        *,
        graph_path: Path | None = None,
        refresh: bool = False,
        include_tests: bool = False,
        top: int = 10,
        dependency_analysis_enabled: bool = False,
        sonarqube_enabled: bool = False,
        keep_sonarqube: bool = False,
        settings: Settings | None = None,
        snapshot: DiscoverySnapshot | None = None,
        assessment: Assessment | None = None,
    ) -> ProjectSummary:
        root = repository.expanduser().resolve()
        if not root.is_dir():
            raise SummaryError(f"Repository does not exist: {root}")
        snapshot = snapshot or discover(root)
        selected_graph = (
            graph_path.expanduser().resolve() if graph_path else root / self.store.relative_path
        )
        if refresh:
            environment = HarnessRepositoryEnvironment(root)
            snapshot = environment.discover(root)
            selected_graph = environment.build_context(root).graph_path
        if not selected_graph.is_file():
            raise SummaryError(
                f"No context graph found at {selected_graph}. Run `refinery analyze {root}` "
                "to build it."
            )
        graph = self.store.load(selected_graph)
        if graph.repository.resolve() != root:
            raise SummaryError(
                f"Context graph belongs to {graph.repository}, not requested repository {root}"
            )
        if assessment is None:
            assessment = AssessmentEngine(
                dependency_analysis_enabled=dependency_analysis_enabled,
                sonarqube_enabled=sonarqube_enabled,
                keep_sonarqube=keep_sonarqube,
                settings=settings,
            ).assess(root)
            write_dependency_artifact(assessment)
        nodes = self._included_nodes(graph, include_tests)
        node_ids = {node.id for node in nodes}
        edges = [edge for edge in graph.edges if edge.source in node_ids or edge.target in node_ids]
        stale = (
            graph.repository_fingerprint is None
            or graph.repository_fingerprint != repository_fingerprint(root)
        )
        return ProjectSummary(
            repository=root,
            overview=self._overview(root, snapshot, assessment.metrics, nodes),
            structure=self._structure(root, snapshot),
            architecture=self._architecture(nodes, edges, top),
            interfaces=self._interfaces(nodes),
            data=self._data(nodes, edges),
            modernization=ModernizationPosture(
                score=assessment.score.overall,
                evidence_completeness=assessment.completeness,
                category_scores={
                    category.value: value for category, value in assessment.score.categories.items()
                },
                metrics=assessment.metrics,
                findings=[
                    finding.message
                    for finding in assessment.findings
                    if finding.category.value != "dependencies"
                ],
                findings_by_category={
                    category.value: [
                        finding.message
                        for finding in assessment.findings
                        if finding.category is category
                    ]
                    for category in assessment.score.categories
                    if category.value != "dependencies"
                },
                analyzer_statuses={
                    name: result.status.value for name, result in assessment.analyzers.items()
                },
                analyzer_diagnostics={
                    name: [diagnostic.message for diagnostic in result.diagnostics]
                    for name, result in assessment.analyzers.items()
                    if result.diagnostics
                },
            ),
            evidence=EvidenceSummary(
                graph_path=selected_graph,
                schema_version=graph.schema_version,
                created_at=graph.created_at,
                node_count=len(nodes),
                edge_count=len(edges),
                provider_runs=graph.provider_runs,
                graph_stale=stale,
            ),
            observations=self._observations(
                snapshot.java_version,
                assessment.metrics,
                stale,
                assessment.analyzers.get("sonarqube"),
                assessment.analyzers.get("dependencies"),
            ),
        )

    @staticmethod
    def _included_nodes(graph: ContextGraph, include_tests: bool) -> list[ContextNode]:
        if include_tests:
            return graph.nodes
        return [
            node
            for node in graph.nodes
            if "src/test" not in str(node.attributes.get("source_file", ""))
            and all("src/test" not in str(source.file) for source in node.sources)
        ]

    @staticmethod
    def _overview(
        root: Path,
        discovery: DiscoverySnapshot,
        metrics: dict[str, object],
        nodes: list[ContextNode],
    ) -> ProjectOverview:
        source = metrics.get("source", {})
        source_metrics = source if isinstance(source, dict) else {}
        frameworks = list(discovery.frameworks)
        kinds = {node.kind for node in nodes}
        if "Spring" in frameworks and "http.endpoint" in kinds and "database.table" in kinds:
            style = "Layered Spring REST application with relational persistence"
        elif "Spring" in frameworks and "http.endpoint" in kinds:
            style = "Spring HTTP application"
        elif "Spring" in frameworks:
            style = "Spring application"
        else:
            style = "Java application; architecture style not confidently inferred"
        return ProjectOverview(
            name=root.name,
            architecture_style=style,
            build_system=discovery.build_system.value,
            java_version=discovery.java_version,
            frameworks=frameworks,
            module_count=len(discovery.modules),
            production_java_files=int(source_metrics.get("productionJavaFiles", 0)),
            test_java_files=int(source_metrics.get("testJavaFiles", 0)),
        )

    @staticmethod
    def _structure(root: Path, snapshot: DiscoverySnapshot) -> ProjectStructure:
        directories = sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir() and path.name not in _IGNORED and not path.name.startswith(".")
        )
        source_roots = sorted(
            str(path.relative_to(root))
            for path in root.rglob("java")
            if path.is_dir() and path.parent.name in {"main", "test"}
        )
        resource_roots = sorted(
            str(path.relative_to(root))
            for path in root.rglob("resources")
            if path.is_dir() and path.parent.name in {"main", "test"}
        )
        migrations = sorted(
            str(path.relative_to(root).parent)
            for path in root.rglob("*.sql")
            if not _IGNORED.intersection(path.relative_to(root).parts)
        )
        return ProjectStructure(
            modules=[str(module.path) for module in snapshot.modules],
            top_level_directories=directories,
            source_roots=source_roots,
            resource_roots=resource_roots,
            migration_locations=sorted(set(migrations)),
        )

    @staticmethod
    def _architecture(
        nodes: list[ContextNode], edges: list[ContextEdge], top: int
    ) -> ArchitectureSummary:
        node_by_id = {node.id: node for node in nodes}
        degrees: Counter[str] = Counter()
        relationships: Counter[str] = Counter()
        for edge in edges:
            source = edge.source
            target = edge.target
            degrees[source] += 1
            degrees[target] += 1
            relationships[edge.kind] += 1
        prominent = [
            ProminentComponent(
                id=node.id,
                name=node.name,
                kind=node.kind,
                relationship_count=count,
                evidence=node.sources[:3],
            )
            for node_id, count in degrees.most_common()
            if (node := node_by_id.get(node_id)) is not None
            and (not node.kind.startswith("code.") or bool(node.attributes.get("source_file")))
        ][:top]
        kinds = Counter(node.kind for node in nodes)
        flow = None
        if relationships["handles"] and relationships["injects"] and kinds["database.table"]:
            flow = "HTTP controller -> injected service/repository -> relational database"
        elif relationships["handles"]:
            flow = "HTTP endpoint -> application component"
        return ArchitectureSummary(
            components_by_kind=dict(sorted(kinds.items())),
            prominent_components=prominent,
            relationship_kinds=dict(sorted(relationships.items())),
            primary_flow=flow,
        )

    @staticmethod
    def _interfaces(nodes: list[ContextNode]) -> ExternalInterfaces:
        return ExternalInterfaces(
            http_endpoints=sorted({node.name for node in nodes if node.kind == "http.endpoint"}),
            kafka_topics=sorted({node.name for node in nodes if node.kind == "kafka.topic"}),
            cache_regions=sorted(
                {node.name for node in nodes if node.kind in {"cache.region", "redis.cache"}}
            ),
            redis_keyspaces=sorted({node.name for node in nodes if node.kind == "redis.keyspace"}),
        )

    @staticmethod
    def _data(nodes: list[ContextNode], edges: list[ContextEdge]) -> DataArchitecture:
        kinds = Counter(edge.kind for edge in edges)
        return DataArchitecture(
            engines=sorted({node.name for node in nodes if node.kind == "database.engine"}),
            connections=sorted({node.name for node in nodes if node.kind == "database.connection"}),
            tables=sorted({node.name for node in nodes if node.kind == "database.table"}),
            entity_mappings=kinds["maps_to"] + kinds["manages_entity"],
            table_references=kinds["references"],
            direct_reads=kinds["reads"],
            direct_writes=kinds["writes"],
        )

    @staticmethod
    def _observations(
        java_version: str | None,
        metrics: dict[str, object],
        stale: bool,
        sonarqube: object | None = None,
        dependencies: object | None = None,
    ) -> list[SummaryObservation]:
        observations: list[SummaryObservation] = []
        if stale:
            observations.append(
                SummaryObservation(
                    severity="warning",
                    category="evidence",
                    message="The context graph may be stale; run analyze again.",
                )
            )
        if java_version and java_version.isdigit() and int(java_version) < 17:
            observations.append(
                SummaryObservation(
                    severity="warning",
                    category="platform",
                    message=(
                        f"Java {java_version} is behind the current long-term-support baselines."
                    ),
                )
            )
        platform = metrics.get("platform", {})
        if isinstance(platform, dict) and platform.get("usesJavax"):
            observations.append(
                SummaryObservation(
                    severity="warning",
                    category="platform",
                    message="Legacy javax imports remain and may complicate Jakarta-era upgrades.",
                )
            )
        status = getattr(sonarqube, "status", None)
        if status is not None and str(status) != "completed":
            diagnostics = getattr(sonarqube, "diagnostics", [])
            detail = diagnostics[0].message if diagnostics else "No diagnostic was provided."
            observations.append(
                SummaryObservation(
                    severity="warning",
                    category="sonarqube",
                    message=f"SonarQube analysis was {status}: {detail}",
                )
            )
        dependency_status = getattr(dependencies, "status", None)
        if dependency_status is not None and str(dependency_status) != "completed":
            diagnostics = getattr(dependencies, "diagnostics", [])
            detail = diagnostics[0].message if diagnostics else "No diagnostic was provided."
            observations.append(
                SummaryObservation(
                    severity="warning",
                    category="dependencies",
                    message=f"Dependency analysis was {dependency_status}: {detail}",
                )
            )
        return observations
