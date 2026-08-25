from pathlib import Path

from refinery_ai.context_graph.models import ContextGraphInfo
from refinery_ai.factory import ModernizationFactory
from refinery_ai.models import ModernizationGoal, RunStatus


class FakeContextGraph:
    def build(self, repository: Path) -> ContextGraphInfo:
        output = repository / "graphify-out"
        output.mkdir(exist_ok=True)
        graph = output / "graph.json"
        graph.write_text('{"nodes": [], "edges": []}')
        return ContextGraphInfo(graph_path=graph)


def test_prepare_persists_reports(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
    report = ModernizationFactory(context_graph=FakeContextGraph()).prepare(
        tmp_path, ModernizationGoal(description="Upgrade Java", target_java=17)
    )
    assert report.status is RunStatus.PLANNED
    assert report.context_graph.provider == "context-graph"
    assert (tmp_path / "graphify-out/graph.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.json").exists()
    assert (tmp_path / f".refinery/runs/{report.run_id}.md").exists()


def test_apply_stops_on_unknown_build_system(tmp_path: Path) -> None:
    report = ModernizationFactory(context_graph=FakeContextGraph()).run(
        tmp_path, ModernizationGoal(description="Upgrade Java"), apply=True
    )
    assert report.status is RunStatus.FAILED
    assert report.steps[0].message == "Unknown build system"
