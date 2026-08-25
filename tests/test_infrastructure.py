from pathlib import Path

from refinery_ai.context_graph.builder import ContextGraphBuilder
from refinery_ai.context_graph.store import ContextGraphStore
from refinery_ai.discovery import discover
from refinery_ai.infrastructure import DatabaseProvider, KafkaProvider, RedisProvider


def test_infrastructure_providers_collect_usage_and_configuration() -> None:
    repository = Path(__file__).parent / "fixtures/infrastructure-context"
    snapshot = discover(repository)
    info = ContextGraphBuilder([DatabaseProvider(), KafkaProvider(), RedisProvider()]).build(
        repository, snapshot
    )
    graph = ContextGraphStore().load(info.graph_path)
    nodes = {node.id: node for node in graph.nodes}
    edges = {(edge.source, edge.target, edge.kind) for edge in graph.edges}
    owner = "java:type:example.InfrastructureClient"

    assert "database:engine:postgresql" in nodes
    assert "database:connection:postgresql" in nodes
    assert "database:table:orders" in nodes
    assert ("database:table:orders", "database:table:customers", "references") in edges
    assert (owner, "database:table:orders", "reads") in edges

    assert "kafka:cluster:configured" in nodes
    assert "kafka:topic:orders.configured" in nodes
    assert (owner, "kafka:topic:orders.created", "publishes") in edges
    assert (owner, "kafka:topic:orders.received", "consumes") in edges

    assert "redis:instance:configured" in nodes
    assert (owner, "redis:keyspace:sessions", "accesses") in edges
    assert ("java:type:example.CachedPerson", "redis:keyspace:people", "maps_to") in edges


def test_infrastructure_providers_skip_unrelated_repository(tmp_path: Path) -> None:
    snapshot = discover(tmp_path)

    assert not DatabaseProvider().supports(snapshot)
    assert not KafkaProvider().supports(snapshot)
    assert not RedisProvider().supports(snapshot)
