import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from refinery_ai.assessment.models import (
    AnalyzerResult,
    AnalyzerStatus,
    Category,
    Finding,
    ToolIdentity,
)
from refinery_ai.cli import app
from refinery_ai.context_graph import ContextGraphStore, repository_fingerprint
from refinery_ai.inspectors.dependencies import DependencyAnalyzer
from refinery_ai.inspectors.sonarqube import SonarQubeAnalyzer
from refinery_ai.models import (
    ContextEdge,
    ContextEvidence,
    ContextGraph,
    ContextNode,
    ProviderRun,
)
from refinery_ai.summary import ProjectSummaryService, SummaryError
from refinery_ai.summary.reporting import summary_markdown


def summary_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "application"
    source = repository / "src/main/java/example"
    tests = repository / "src/test/java/example"
    source.mkdir(parents=True)
    tests.mkdir(parents=True)
    (repository / "pom.xml").write_text(
        """<project><modelVersion>4.0.0</modelVersion>
        <properties><java.version>11</java.version></properties>
        <dependencies><dependency><groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>"""
    )
    (source / "Orders.java").write_text("package example; class Orders {}")
    (tests / "OrdersTest.java").write_text("package example; class OrdersTest {}")
    evidence = ContextEvidence(
        provider="spring", file=Path("src/main/java/example/Orders.java"), line=1
    )
    graph = ContextGraph(
        repository=repository,
        repository_fingerprint=repository_fingerprint(repository),
        nodes=[
            ContextNode(
                id="spring:bean:orders",
                kind="spring.bean",
                name="orders",
                sources=[evidence],
            ),
            ContextNode(
                id="http:endpoint:GET:/orders",
                kind="http.endpoint",
                name="GET /orders",
                sources=[evidence],
            ),
            ContextNode(
                id="database:table:orders",
                kind="database.table",
                name="orders",
                sources=[evidence],
            ),
        ],
        edges=[
            ContextEdge(
                source="spring:bean:orders",
                target="http:endpoint:GET:/orders",
                kind="handles",
                sources=[evidence],
            ),
            ContextEdge(
                source="spring:bean:orders",
                target="database:table:orders",
                kind="injects",
                sources=[evidence],
            ),
        ],
        provider_runs=[
            ProviderRun(provider="spring", node_count=3, edge_count=2),
            ProviderRun(provider="kafka", status="skipped"),
        ],
    )
    ContextGraphStore().save(graph)
    return repository


def test_summary_combines_graph_discovery_structure_and_assessment(tmp_path: Path) -> None:
    repository = summary_repository(tmp_path)

    summary = ProjectSummaryService().summarize(repository, top=2)
    rendered = summary_markdown(summary)

    assert summary.overview.architecture_style.startswith("Layered Spring REST")
    assert summary.overview.production_java_files == 1
    assert summary.overview.test_java_files == 1
    assert summary.interfaces.http_endpoints == ["GET /orders"]
    assert summary.data.tables == ["orders"]
    assert len(summary.architecture.prominent_components) == 2
    assert not summary.evidence.graph_stale
    assert "Modernization posture" in rendered
    assert "kafka: skipped" in rendered


def test_summary_detects_stale_and_wrong_graphs(tmp_path: Path) -> None:
    repository = summary_repository(tmp_path)
    (repository / "src/main/java/example/Orders.java").write_text(
        "package example; class Orders { int changed; }"
    )

    assert ProjectSummaryService().summarize(repository).evidence.graph_stale

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(SummaryError, match="belongs to"):
        ProjectSummaryService().summarize(
            other, graph_path=repository / ".refinery/context/context-graph.json"
        )


def test_summary_missing_graph_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(SummaryError, match="--refresh"):
        ProjectSummaryService().summarize(tmp_path)


def test_summary_cli_emits_json(tmp_path: Path) -> None:
    repository = summary_repository(tmp_path)

    result = CliRunner().invoke(
        app,
        ["summary", str(repository), "--json", "--no-sonarqube", "--no-dependencies"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["overview"]["name"] == "application"
    persisted = repository / ".refinery/summary.json"
    assert json.loads(persisted.read_text())["overview"]["name"] == "application"


def test_summary_cli_persists_markdown_in_target_repository(tmp_path: Path) -> None:
    repository = summary_repository(tmp_path)

    result = CliRunner().invoke(
        app, ["summary", str(repository), "--no-sonarqube", "--no-dependencies"]
    )

    assert result.exit_code == 0
    persisted = repository / ".refinery/summary.md"
    assert persisted.read_text() == result.stdout
    assert result.stdout.startswith("# Project Architecture Summary")


def test_summary_markdown_includes_dependency_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = summary_repository(tmp_path)

    def completed(self: DependencyAnalyzer, repository: Path, snapshot: object) -> AnalyzerResult:
        return AnalyzerResult(
            analyzer="dependencies",
            status=AnalyzerStatus.COMPLETED,
            completeness=1,
            duration_seconds=0,
            tool=ToolIdentity(name="dependencies", version="1", configuration_hash="x"),
            metrics={
                "dependencyCount": 14,
                "directDependencyCount": 3,
                "vulnerableDependencyCount": 1,
                "vulnerabilityCount": 1,
                "criticalVulnerabilityCount": 0,
                "highVulnerabilityCount": 1,
                "mediumVulnerabilityCount": 0,
                "lowVulnerabilityCount": 0,
                "unknownVulnerabilityCount": 0,
            },
            findings=[
                Finding(
                    fingerprint="osv:GHSA-example:pkg:maven/org.example/library@1",
                    analyzer="dependencies",
                    rule="GHSA-example",
                    category=Category.DEPENDENCIES,
                    severity="high",
                    message="org.example:library:1 is affected by GHSA-example",
                )
            ],
        )

    monkeypatch.setattr(DependencyAnalyzer, "analyze", completed)

    result = CliRunner().invoke(app, ["summary", str(repository), "--no-sonarqube"])

    assert result.exit_code == 0
    assert "Dependency Health: 90/100" in result.stdout
    assert "### Dependency analysis" in result.stdout
    assert "Resolved dependencies: 14 (3 direct)" in result.stdout
    assert "Vulnerable dependencies: 1" in result.stdout
    assert "org.example:library:1 is affected by GHSA-example" not in result.stdout
    artifacts = list((repository / ".refinery/dependencies").glob("osv-findings-*.json"))
    assert len(artifacts) == 1
    artifact = json.loads(artifacts[0].read_text())
    assert artifact["findings"][0]["rule"] == "GHSA-example"
    assert str(Path(".refinery/dependencies") / artifacts[0].name) in result.stdout


def test_summary_cli_defaults_sonarqube_on_and_allows_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = summary_repository(tmp_path)
    monkeypatch.setenv("REFINERY_DEPENDENCY_ANALYSIS_ENABLED", "false")
    calls = 0

    def unavailable(self: SonarQubeAnalyzer, repository: Path, snapshot: object) -> AnalyzerResult:
        nonlocal calls
        calls += 1
        return AnalyzerResult(
            analyzer="sonarqube",
            status=AnalyzerStatus.UNAVAILABLE,
            completeness=0,
            duration_seconds=0,
            tool=ToolIdentity(name="sonarqube", version="none", configuration_hash="x"),
        )

    monkeypatch.setattr(SonarQubeAnalyzer, "analyze", unavailable)
    runner = CliRunner()

    default = runner.invoke(app, ["summary", str(repository)])
    disabled = runner.invoke(app, ["summary", str(repository), "--no-sonarqube"])

    assert default.exit_code == 0
    assert disabled.exit_code == 0
    assert calls == 1
