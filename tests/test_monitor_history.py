"""History is derived from journal evidence, never from the planned list."""

from pathlib import Path

import pytest
from test_migration_events import state
from test_monitor_migration_labels import RECIPES
from test_profile_run import report

from refinery_ai.migrations.events import MigrationReporter, read_migration_events
from refinery_ai.models import RecipePlan
from refinery_ai.monitor.journals import JournalCatalog
from refinery_ai.monitor.models import MonitorEvent
from refinery_ai.monitor.recording import InvocationContext, parent_invocation


def event(sequence: int, phase: str, lifecycle: str = "completed", **extra) -> MonitorEvent:
    return MonitorEvent.model_validate(
        {
            "schema_version": 1,
            "run_id": "a" * 32,
            "sequence": sequence,
            "timestamp": "2026-11-17T12:00:00Z",
            "event_id": f"e{sequence}",
            "execution_id": f"x{sequence}",
            "phase": phase,
            "lifecycle": lifecycle,
            "generation": 0,
            "step": 1,
            "recipe": "example.Java17",
            "migration_name": "Java 17",
            "apply": True,
            **extra,
        }
    )


def history(tmp_path: Path, events: list[MonitorEvent]):
    path = tmp_path / ".refinery/migration-events" / f"{'a' * 32}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(e.model_dump_json() + "\n" for e in events))
    run = JournalCatalog(tmp_path, Path(".refinery")).snapshot("a" * 32).run
    assert hasattr(run, "execution_history"), "Run summary must expose durable history"
    return run.execution_history


@pytest.mark.parametrize(
    ("last", "expected"),
    [
        (event(2, "execute_recipe"), "applied"),
        (event(2, "checkpoint_step", checkpoint="commit"), "verified"),
        (event(2, "checkpoint_step", apply=False), "previewed"),
        (event(2, "checkpoint_step", apply=None, checkpoint=None), "completed"),
        (event(2, "execute_recipe", failure_reason="Command failed"), "failed"),
        (event(2, "prepare_step", outcome="skipped"), "skipped"),
        (event(2, "execute_recipe", "cancelled"), "cancelled"),
        (event(2, "execute_recipe", "interrupted"), "interrupted"),
    ],
)
def test_outcomes_require_evidence(tmp_path: Path, last: MonitorEvent, expected: str):
    entries = history(tmp_path, [event(1, "prepare_step", "started"), last])
    assert len(entries) == 1
    assert entries[0].status == expected
    assert entries[0].label == "Java 17"


def test_build_failure_repair_and_checkpoint_share_one_attempt(tmp_path: Path):
    events = [
        event(1, "prepare_step", "started"),
        event(2, "execute_recipe"),
        event(3, "verify_candidate", outcome="verification_failed"),
    ]
    assert history(tmp_path, events)[0].status == "failed"
    events.append(event(4, "repair", "started"))
    assert history(tmp_path, events)[0].status == "active"
    events += [
        event(5, "verify_candidate", outcome="verification_passed"),
        event(6, "checkpoint_step", checkpoint="commit"),
    ]
    entries = history(tmp_path, events)
    assert [(e.attempt, e.status) for e in entries] == [(1, "verified")]


def test_recovery_preserves_attempts_and_original_parent(tmp_path: Path):
    entries = history(
        tmp_path,
        [
            event(1, "prepare_step", "started", parent_execution_id="old"),
            event(2, "execute_recipe", "interrupted", parent_execution_id="new"),
            event(3, "prepare_step", "started", generation=1, parent_execution_id="new"),
            event(4, "checkpoint_step", generation=1, checkpoint="commit"),
            event(5, "prepare_step", "started", generation=1, step=2),
        ],
    )
    assert [(e.generation, e.attempt, e.status) for e in entries] == [
        (0, 1, "interrupted"),
        (1, 2, "verified"),
        (1, 3, "active"),
    ]
    assert entries[0].parent_execution_id == "old"
    assert len({e.id for e in entries}) == 3


def test_no_history_for_plans_usage_or_run_level_step_metadata(tmp_path: Path):
    assert (
        history(
            tmp_path,
            [
                event(1, "prepare_run", migrations=[{"recipe": "x", "name": "Planned"}]),
                event(2, "repair", "usage"),
                event(3, "finish_run"),
            ],
        )
        == []
    )


def test_run_termination_closes_only_unfinished_entries(tmp_path: Path):
    entries = history(
        tmp_path,
        [
            event(1, "checkpoint_step", checkpoint="commit"),
            event(2, "prepare_step", "started", step=2),
            event(3, "run", "cancelled", step=None, recipe=None),
        ],
    )
    assert [e.status for e in entries] == ["verified", "cancelled"]


def test_reporter_persists_apply_mode_and_history_survives_reopen(tmp_path: Path):
    value = state(tmp_path)
    plan = report(tmp_path, "plan", RECIPES).plan
    assert isinstance(plan, RecipePlan)
    value.plan.steps = plan.recommendations
    value.index = 1
    value.apply = False
    path = tmp_path / ".refinery/migration-events" / f"{value.run_id}.jsonl"
    with parent_invocation(InvocationContext("b" * 32, "parent")):
        reporter = MigrationReporter(path, None)
        reporter.emit("prepare_step", "started", value, "prepare")
        reporter.emit("checkpoint_step", "completed", value, "checkpoint")
        reporter.close()
    assert getattr(read_migration_events(path)[0], "apply", None) is False
    catalog = JournalCatalog(tmp_path, Path(".refinery"))
    first = catalog.snapshot(value.run_id).run.execution_history
    assert first[0].status == "previewed"
    assert first[0].parent_execution_id == "parent"
    assert JournalCatalog(tmp_path, Path(".refinery")).list_runs()[0].execution_history == first
