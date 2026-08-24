import json
from pathlib import Path

from refinery_ai.context_graph import (
    ContextFragment,
    ContextGraphBuilder,
    ContextGraphQuery,
    ContextRequest,
)
from refinery_ai.models import ContextEdge, ContextEvidence, ContextNode


class FirstProvider:
    name = "first"

    def supports(self, discovery: object | None) -> bool:
        return True

    def collect(self, request: ContextRequest) -> ContextFragment:
        return ContextFragment(
            provider=self.name,
            nodes=[
                ContextNode(
                    id="java:type:example.OrderService",
                    kind="java.type",
                    name="OrderService",
                    attributes={"language": "java"},
                    sources=[ContextEvidence(provider=self.name, file=Path("OrderService.java"))],
                )
            ],
        )


class SecondProvider:
    name = "second"

    def supports(self, discovery: object | None) -> bool:
        return True

    def collect(self, request: ContextRequest) -> ContextFragment:
        return ContextFragment(
            provider=self.name,
            nodes=[
                ContextNode(
                    id="java:type:example.OrderService",
                    kind="spring.bean",
                    name="OrderService",
                    attributes={"bean": "orderService"},
                    sources=[ContextEvidence(provider=self.name)],
                )
            ],
            edges=[
                ContextEdge(
                    source="http:endpoint:POST:/orders",
                    target="java:type:example.OrderService",
                    kind="injects",
                    sources=[ContextEvidence(provider=self.name)],
                )
            ],
        )


def test_builder_merges_provider_fragments_with_provenance(tmp_path: Path) -> None:
    info = ContextGraphBuilder([FirstProvider(), SecondProvider()]).build(tmp_path)
    graph = json.loads(info.graph_path.read_text())

    assert info.node_count == 1
    assert info.edge_count == 1
    assert [run.provider for run in info.provider_runs] == ["first", "second"]
    assert graph["nodes"][0]["attributes"] == {"language": "java", "bean": "orderService"}
    assert [source["provider"] for source in graph["nodes"][0]["sources"]] == [
        "first",
        "second",
    ]


def test_query_reads_normalized_graph(tmp_path: Path) -> None:
    info = ContextGraphBuilder([FirstProvider(), SecondProvider()]).build(tmp_path)

    result = json.loads(ContextGraphQuery().query(info.graph_path, "OrderService"))

    assert result["nodes"][0]["name"] == "OrderService"
    assert result["edges"][0]["kind"] == "injects"
