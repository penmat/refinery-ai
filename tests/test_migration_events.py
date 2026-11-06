from pathlib import Path

import pytest

from refinery_ai.migrations.events import MigrationReporter, read_migration_events
from refinery_ai.migrations.execution_state import MigrationExecutionState
from refinery_ai.models import BuildSystem, DiscoverySnapshot, PersistedMigrationPlan


def state(root: Path) -> MigrationExecutionState:
    return MigrationExecutionState(
        plan=PersistedMigrationPlan(
            plan_id="plan",
            snapshot=DiscoverySnapshot(repository=root, build_system=BuildSystem.MAVEN),
            steps=[],
        ),
        run_id="a" * 32,
        apply=True,
        publish=True,
    )


def test_journal_replays_sequence_and_repairs_only_partial_tail(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    reporter = MigrationReporter(path, None)
    reporter.emit("repair", "started", state(tmp_path), "execution")
    reporter.close()
    with path.open("ab") as output:
        output.write(b'{"incomplete":')
    reporter = MigrationReporter(path, None)
    reporter.emit("repair", "interrupted", state(tmp_path), "execution")
    reporter.close()
    events = read_migration_events(path)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.lifecycle for event in events] == ["started", "interrupted"]
    assert events[0].execution_id == events[1].execution_id


def test_observer_failure_does_not_prevent_journal(tmp_path: Path) -> None:
    def bad(event):
        raise RuntimeError("observer unavailable")

    path = tmp_path / "events.jsonl"
    reporter = MigrationReporter(path, bad)
    reporter.emit("repair", "completed", state(tmp_path), "one", outcome="accepted")
    reporter.close()
    assert read_migration_events(path)[0].outcome == "accepted"


def test_journal_failure_between_repair_and_verification_restores_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    from test_migration_recovery import setup_run

    from refinery_ai.migrations.repair import MigrationRepairStage
    from refinery_ai.migrations.runner import MigrationRunner
    from refinery_ai.models import ExecutionType, VerificationResult

    store = setup_run(tmp_path)

    class Recipes:
        def run(self, snapshot, step, **kwargs):
            (snapshot.repository / "change").write_text("recipe")
            return VerificationResult(command=["recipe"], exit_code=0, duration_seconds=0)

    class Repair:
        root = None

        def repair(self, repository, *args):
            self.root = repository
            (repository / "change").write_text("unverified")
            (repository / "new-source").write_text("candidate")
            return VerificationResult(command=["repair"], exit_code=0, duration_seconds=0)

        def rollback_last(self):
            (self.root / "change").write_text("recipe")
            (self.root / "new-source").unlink()

        def accept_last(self):
            pass

    calls = 0

    def verify(*args, **kwargs):
        nonlocal calls
        calls += 1
        return VerificationResult(
            command=["test"],
            exit_code=0 if calls == 1 else 1,
            stderr="credential-sentinel",
            duration_seconds=0,
        )

    original = MigrationReporter.emit

    def emit(self, phase, lifecycle, state, *args, **kwargs):
        if phase == "verify_candidate" and lifecycle == "started" and state.pending:
            raise OSError("journal unavailable")
        return original(self, phase, lifecycle, state, *args, **kwargs)

    monkeypatch.setattr(MigrationReporter, "emit", emit)
    repair = Repair()
    runner = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: Recipes()},
        build_verifier=verify,
        repair_stages=(MigrationRepairStage("fake", repair, 1, uses_model=True),),
    )
    with pytest.raises(OSError, match="journal unavailable"):
        runner.run(apply=True)
    assert (repair.root / "change").read_text() == "recipe"
    assert not (repair.root / "new-source").exists()
    journal = next((tmp_path / ".refinery/migration-events").glob("*.jsonl"))
    assert "credential-sentinel" not in journal.read_text()
