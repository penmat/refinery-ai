from pathlib import Path

import pytest

from refinery_ai.assessment.engine import AssessmentEngine
from refinery_ai.assessment.models import Category, Finding
from refinery_ai.assessment.storage import AssessmentStore
from refinery_ai.context_graph.models import (
    ContextEdge,
    ContextEvidence,
    ContextGraph,
    ContextNode,
    ProviderRun,
)
from refinery_ai.context_graph.store import ContextGraphStore
from refinery_ai.llm import tool_definition
from refinery_ai.tools import RepositoryEvidenceTools


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "pom.xml").write_text(
        """<project>
        <modelVersion>4.0.0</modelVersion>
        <properties><java.version>11</java.version></properties>
        </project>"""
    )
    return root


def save_assessment(root: Path) -> None:
    assessment = AssessmentEngine().assess(root, name="latest")
    findings = [
        Finding(
            fingerprint=f"finding-{index}",
            analyzer="source",
            rule="java:S100",
            category=Category.MAINTAINABILITY,
            severity="high" if index % 2 else "medium",
            message=f"Order service issue {index}",
            path="src/main/java/example/OrderService.java",
            line=index + 1,
        )
        for index in range(5)
    ]
    assessment.findings = findings
    assessment.analyzers["source"].findings = findings
    AssessmentStore(root).save(assessment)


def save_graph(root: Path) -> Path:
    source = ContextEvidence(
        provider="spring", file=Path("src/main/java/example/OrderController.java"), line=12
    )
    graph = ContextGraph(
        repository=root,
        repository_fingerprint="sha256:test",
        nodes=[
            ContextNode(
                id="http:endpoint:GET:/orders",
                kind="http.endpoint",
                name="GET /orders",
                sources=[source],
            ),
            ContextNode(
                id="java:type:example.OrderController",
                kind="spring.bean",
                name="OrderController",
                sources=[source],
            ),
            ContextNode(
                id="java:type:example.OrderService",
                kind="spring.bean",
                name="OrderService",
                sources=[source],
            ),
            ContextNode(
                id="database:table:orders",
                kind="database.table",
                name="orders",
                sources=[source],
            ),
        ],
        edges=[
            ContextEdge(
                source="http:endpoint:GET:/orders",
                target="java:type:example.OrderController",
                kind="handled_by",
                sources=[source],
            ),
            ContextEdge(
                source="java:type:example.OrderController",
                target="java:type:example.OrderService",
                kind="injects",
                sources=[source],
            ),
            ContextEdge(
                source="java:type:example.OrderService",
                target="java:type:example.OrderController",
                kind="calls",
                sources=[source],
            ),
            ContextEdge(
                source="java:type:example.OrderService",
                target="database:table:orders",
                kind="reads",
                sources=[source],
            ),
        ],
        provider_runs=[ProviderRun(provider="spring", node_count=4, edge_count=4)],
    )
    return ContextGraphStore().save(graph).graph_path


def test_core_catalog_produces_litellm_tool_schemas(tmp_path: Path) -> None:
    evidence = RepositoryEvidenceTools(repository(tmp_path))

    definitions = [tool_definition(tool)["function"] for tool in evidence.tools()]

    assert [definition["name"] for definition in definitions] == [
        "get_repository_overview",
        "list_symbols",
        "find_symbol",
        "explain_symbol",
        "get_callers",
        "get_callees",
        "get_dependencies",
        "get_dependents",
        "search_findings",
        "get_inspector_result",
    ]
    callers = definitions[4]["parameters"]
    assert callers["properties"]["symbol_id"]["description"].startswith("Exact symbol ID")
    assert callers["required"] == ["symbol_id"]


def test_overview_and_inspector_tools_use_latest_assessment(tmp_path: Path) -> None:
    root = repository(tmp_path)
    save_assessment(root)
    save_graph(root)
    evidence = RepositoryEvidenceTools(root)

    overview = evidence.get_repository_overview()
    listing = evidence.list_inspector_results()
    source = evidence.get_inspector_result("source", include_findings=True, finding_limit=2)

    assert overview["build_system"] == "maven"
    assert overview["java_version"] == "11"
    assert overview["graph_node_count"] == 4
    assert overview["graph_providers"][0]["provider"] == "spring"
    assert listing["available"] is True
    assert {item["inspector"] for item in listing["inspectors"]} >= {"build", "source"}
    assert source["total_findings"] == 5
    assert source["returned_findings"] == 2
    assert source["findings_truncated"] is True


def test_unavailable_persisted_evidence_is_explicit(tmp_path: Path) -> None:
    evidence = RepositoryEvidenceTools(repository(tmp_path))

    assert evidence.list_inspector_results()["available"] is False
    assert evidence.get_inspector_result("sonarqube")["available"] is False
    assert evidence.search_findings()["available"] is False
    assert evidence.search_context("anything")["available"] is False


def test_finding_search_filters_and_bounds_results(tmp_path: Path) -> None:
    root = repository(tmp_path)
    save_assessment(root)
    evidence = RepositoryEvidenceTools(root)

    result = evidence.search_findings(
        query="order issue", inspector="SOURCE", severity="high", limit=1
    )

    assert result["matched_count"] == 2
    assert result["returned_count"] == 1
    assert result["truncated"] is True
    assert result["findings"][0]["severity"] == "high"


def test_context_search_filters_kinds_and_keeps_provenance(tmp_path: Path) -> None:
    root = repository(tmp_path)
    graph_path = save_graph(root)
    evidence = RepositoryEvidenceTools(root, graph_path=graph_path)

    result = evidence.search_context("OrderService", node_kinds=["spring.bean"])

    assert result["matched_node_count"] == 1
    assert result["nodes"][0]["id"] == "java:type:example.OrderService"
    assert result["nodes"][0]["sources"][0]["line"] == 12
    assert {edge["kind"] for edge in result["edges"]} == {"injects", "calls", "reads"}
    assert result["repository_fingerprint"] == "sha256:test"


def test_context_neighborhood_is_directional_bounded_and_cycle_safe(tmp_path: Path) -> None:
    root = repository(tmp_path)
    graph_path = save_graph(root)
    evidence = RepositoryEvidenceTools(root, graph_path=graph_path)

    outgoing = evidence.get_context_neighborhood(
        "java:type:example.OrderController", direction="outgoing", depth=2
    )
    incoming = evidence.get_context_neighborhood(
        "java:type:example.OrderController",
        direction="incoming",
        edge_kinds=["handled_by"],
    )

    assert {node["name"] for node in outgoing["nodes"]} == {
        "OrderController",
        "OrderService",
        "orders",
    }
    assert outgoing["matched_edge_count"] == 3
    assert {node["name"] for node in incoming["nodes"]} == {
        "GET /orders",
        "OrderController",
    }
    with pytest.raises(ValueError, match="depth"):
        evidence.get_context_neighborhood("anything", depth=4)
    with pytest.raises(ValueError, match="limit"):
        evidence.search_context("anything", limit=0)


def test_semantic_symbol_flow_explains_calls_and_dependencies(tmp_path: Path) -> None:
    root = repository(tmp_path)
    graph_path = save_graph(root)
    evidence = RepositoryEvidenceTools(root, graph_path=graph_path)

    found = evidence.find_symbol("OrderService")
    symbol_id = found["symbols"][0]["id"]
    explanation = evidence.explain_symbol(symbol_id)
    callers = evidence.get_callers(symbol_id)
    callees = evidence.get_callees(symbol_id)
    dependencies = evidence.get_dependencies(symbol_id, dependency_types=["database"])
    dependents = evidence.get_dependents(symbol_id, dependency_types=["injection"])

    assert found["matched_count"] == 1
    assert explanation["symbol"]["name"] == "OrderService"
    assert callers["coverage"] == "call-level"
    assert callers["results"] == []
    assert callees["results"][0]["symbol"]["name"] == "OrderController"
    assert dependencies["results"][0]["symbol"]["name"] == "orders"
    assert dependencies["results"][0]["relationship_type"] == "database"
    assert dependents["results"][0]["symbol"]["name"] == "OrderController"


def test_list_symbols_supports_discovery_and_kind_filtering(tmp_path: Path) -> None:
    root = repository(tmp_path)
    graph_path = save_graph(root)
    evidence = RepositoryEvidenceTools(root, graph_path=graph_path)

    inventory = evidence.list_symbols(limit=2)
    endpoints = evidence.list_symbols(kind="HTTP.ENDPOINT")
    unknown = evidence.list_symbols(kind="code.method")

    assert inventory["available_kinds"] == {
        "database.table": 1,
        "http.endpoint": 1,
        "spring.bean": 2,
    }
    assert inventory["matched_count"] == 4
    assert inventory["returned_count"] == 2
    assert inventory["truncated"] is True
    assert endpoints["symbols"][0]["name"] == "GET /orders"
    assert unknown["available"] is True
    assert unknown["matched_count"] == 0
    assert "available_kinds" in unknown["reason"]


def test_list_symbols_reports_missing_graph(tmp_path: Path) -> None:
    evidence = RepositoryEvidenceTools(repository(tmp_path))

    result = evidence.list_symbols()

    assert result["available"] is False
    assert result["symbols"] == []


def test_call_tools_warn_when_graph_has_no_call_evidence(tmp_path: Path) -> None:
    root = repository(tmp_path)
    graph = ContextGraph(
        repository=root,
        nodes=[ContextNode(id="code:Service", kind="code.class", name="Service")],
    )
    graph_path = ContextGraphStore().save(graph).graph_path
    evidence = RepositoryEvidenceTools(root, graph_path=graph_path)

    result = evidence.get_callers("code:Service")

    assert result["coverage"] == "relationship-level"
    assert "does not prove" in result["warning"]


def test_semantic_tools_validate_ids_depth_and_dependency_types(tmp_path: Path) -> None:
    root = repository(tmp_path)
    graph_path = save_graph(root)
    evidence = RepositoryEvidenceTools(root, graph_path=graph_path)

    assert evidence.explain_symbol("missing")["available"] is False
    assert evidence.get_dependencies("missing")["available"] is False
    with pytest.raises(ValueError, match="depth"):
        evidence.get_callers("anything", depth=4)
    with pytest.raises(ValueError, match="Unknown dependency types"):
        evidence.get_dependencies("anything", dependency_types=["magic"])
