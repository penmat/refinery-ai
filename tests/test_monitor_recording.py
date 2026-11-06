import json
from pathlib import Path

import pytest
from test_migration_events import state
from test_profile_run import report

from refinery_ai.migrations.events import MigrationReporter
from refinery_ai.models import MigrationRun, RunStatus
from refinery_ai.monitor.recording import InvocationContext, parent_invocation
from refinery_ai.monitor.topology import graph_topology, save_topology
from refinery_ai.profile_run import ProfileRunner
from refinery_ai.profiles import load_profile
from refinery_ai.workflows.migration import build_migration_graph


def test_child_identity_is_optional_and_context_scoped(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    reporter = MigrationReporter(path, None)
    with parent_invocation(InvocationContext("b" * 32, "parent-node")):
        event = reporter.emit("repair", "started", state(tmp_path), "child")
    standalone = reporter.emit("repair", "completed", state(tmp_path), "child")
    reporter.close()
    assert event.parent_run_id == "b" * 32
    assert event.parent_execution_id == "parent-node"
    assert standalone.parent_run_id is None


def test_topology_extraction_does_not_run_migration(tmp_path: Path) -> None:
    topology = graph_topology(build_migration_graph(None), "migration")
    assert "repair" in [node.id for node in topology.nodes]
    assert ("repair", "verify_candidate") in [(e.source, e.target) for e in topology.edges]
    save_topology(tmp_path, "a" * 32, topology)
    assert (
        json.loads((tmp_path / "monitor-topologies" / ("a" * 32 + ".json")).read_text())["kind"]
        == "migration"
    )


def test_profile_resume_preserves_identity_and_records_visits(tmp_path: Path) -> None:
    calls = 0

    def plan():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("interrupted")
        return report(tmp_path, "plan", [])

    runner = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda _: True,
        plan=plan,
        migrate=lambda *_: MigrationRun(plan_id="plan", status=RunStatus.COMPLETED),
    )
    with pytest.raises(RuntimeError):
        runner.run()
    runner.run()
    files = list((tmp_path / ".refinery/profile-events").glob("*.jsonl"))
    assert len(files) == 1
    events = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert len([e for e in events if e["phase"] == "analyze" and e["lifecycle"] == "started"]) == 1
    visits = [e for e in events if e["phase"] == "plan" and e["lifecycle"] == "started"]
    assert len({e["execution_id"] for e in visits}) == 2
    assert [e["generation"] for e in visits] == [0, 1]
    assert events[-1]["phase"] == "run"
    assert events[-1]["outcome"] == "completed"


def test_profile_reporting_failure_does_not_mask_original_exception(tmp_path, monkeypatch):
    from refinery_ai.monitor.recording import ProfileReporter

    original = ProfileReporter.emit

    def emit(self, phase, lifecycle, *args, **kwargs):
        if lifecycle == "failed":
            raise OSError("reporter unavailable")
        return original(self, phase, lifecycle, *args, **kwargs)

    monkeypatch.setattr(ProfileReporter, "emit", emit)

    def fail(_):
        raise RuntimeError("original failure")

    runner = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=fail,
        plan=lambda: report(tmp_path, "plan", []),
        migrate=lambda *_: MigrationRun(plan_id="plan", status=RunStatus.COMPLETED),
    )
    with pytest.raises(RuntimeError, match="original failure"):
        runner.run()


def test_actual_profile_invocation_correlates_child_events(tmp_path):
    from refinery_ai.migrations.events import read_migration_events

    path = tmp_path / ".refinery/migration-events" / ("a" * 32 + ".jsonl")
    calls = 0

    def plan():
        nonlocal calls
        calls += 1
        return report(tmp_path, "plan", ["recipe"] if calls == 1 else [])

    def migrate(*_):
        reporter = MigrationReporter(path, None)
        reporter.emit("execute_recipe", "started", state(tmp_path), "child")
        reporter.close()
        return MigrationRun(plan_id="plan", status=RunStatus.COMPLETED)

    runner = ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda _: True,
        plan=plan,
        migrate=migrate,
    )
    runner.run()
    child = read_migration_events(path)[0]
    parent_path = tmp_path / ".refinery/profile-events" / f"{child.parent_run_id}.jsonl"
    parent = [json.loads(line) for line in parent_path.read_text().splitlines()]
    assert any(
        e["execution_id"] == child.parent_execution_id and e["phase"] == "migrate" for e in parent
    )
