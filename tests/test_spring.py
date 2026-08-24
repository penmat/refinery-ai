from pathlib import Path

from refinery_ai.context_graph import ContextGraphBuilder, ContextGraphStore
from refinery_ai.discovery import discover
from refinery_ai.spring import SpringProvider


def test_spring_provider_collects_framework_semantics() -> None:
    repository = Path(__file__).parent / "fixtures/spring-context"
    info = ContextGraphBuilder([SpringProvider()]).build(repository, discover(repository))
    graph = ContextGraphStore().load(info.graph_path)
    nodes = {node.id: node for node in graph.nodes}
    edges = {(edge.source, edge.target, edge.kind) for edge in graph.edges}

    assert "spring:bean:orderController" in nodes
    assert "spring:bean:orderService" in nodes
    assert "http:endpoint:POST:/orders" in nodes
    assert "database:table:orders" in nodes
    assert "kafka:topic:orders.created" in nodes
    assert "cache:region:orders" in nodes
    assert "configuration:property:orders.region" in nodes
    assert "configuration:property:orders.client" in nodes
    assert (
        "spring:bean:orderController",
        "java:type:example.OrderService",
        "injects",
    ) in edges
    assert (
        "java:type:example.Order",
        "database:table:orders",
        "maps_to",
    ) in edges
    assert (
        "java:type:example.OrderRepository",
        "java:type:example.Order",
        "manages_entity",
    ) in edges


def test_spring_provider_skips_non_spring_discovery(tmp_path: Path) -> None:
    snapshot = discover(tmp_path)

    assert not SpringProvider().supports(snapshot)
