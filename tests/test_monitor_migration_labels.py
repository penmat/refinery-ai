import json
from pathlib import Path

from test_migration_events import state
from test_profile_run import report

from refinery_ai.migrations.events import MigrationReporter, read_migration_events
from refinery_ai.models import MigrationRun, RecipePlan, RunStatus
from refinery_ai.monitor.journals import JournalCatalog
from refinery_ai.profile_run import ProfileRunner
from refinery_ai.profiles import load_profile

RECIPES = [
    "org.example.SpringFoxToSpringDoc",
    "org.example.UpgradeToJava17",
    "org.example.ThirdMigration",
    "org.example.FourthMigration",
]


def test_profile_plan_records_ordered_migration_names(tmp_path: Path) -> None:
    plans = iter([report(tmp_path, "first", RECIPES), report(tmp_path, "done", [])])
    ProfileRunner(
        tmp_path,
        load_profile("spring-boot-3.2-java-17"),
        analyze=lambda _: True,
        plan=lambda: next(plans),
        migrate=lambda *_: MigrationRun(plan_id="first", status=RunStatus.COMPLETED),
    ).run()
    catalog = JournalCatalog(tmp_path, Path(".refinery"))
    events = catalog.snapshot(catalog.list_runs()[0].run_id).events
    plan = next(e for e in events if e.phase == "plan" and e.lifecycle == "completed")
    assert [item.recipe for item in plan.migrations] == RECIPES
    migrate = next(e for e in events if e.phase == "migrate" and e.lifecycle == "started")
    assert migrate.migrations == plan.migrations
    assert [item.name for item in plan.migrations][:2] == [
        "Spring Fox → Spring Doc",
        "Upgrade to Java 17",
    ]


def test_migration_events_and_summary_include_name_and_position(tmp_path: Path) -> None:
    value = state(tmp_path)
    plan = report(tmp_path, "plan", RECIPES).plan
    assert isinstance(plan, RecipePlan)
    value.plan.steps = plan.recommendations
    value.index = 2
    path = tmp_path / ".refinery/migration-events" / f"{value.run_id}.jsonl"
    reporter = MigrationReporter(path, None)
    baseline = reporter.emit("verify_baseline", "started", value, "baseline")
    assert baseline.migration_name is None
    reporter.emit("prepare_step", "started", value, "step-two")
    reporter.close()
    event = read_migration_events(path)[1]
    assert (event.migration_name, event.step, event.total_steps) == ("Upgrade to Java 17", 2, 4)
    run = JournalCatalog(tmp_path, Path(".refinery")).snapshot(value.run_id).run
    assert (run.migration_name, run.step, run.total_steps) == ("Upgrade to Java 17", 2, 4)
    # Older journals must still load without new optional fields.
    payload = json.loads(path.read_text().splitlines()[1])
    payload["sequence"] = 1
    for key in ("migration_name", "total_steps", "migrations"):
        payload.pop(key, None)
    path.write_text(json.dumps(payload) + "\n")
    assert read_migration_events(path)[0].recipe == RECIPES[1]


def test_current_recipe_summary_does_not_inherit_previous_failure(tmp_path: Path) -> None:
    value = state(tmp_path)
    plan = report(tmp_path, "plan", RECIPES).plan
    assert isinstance(plan, RecipePlan)
    value.plan.steps = plan.recommendations
    path = tmp_path / ".refinery/migration-events" / f"{value.run_id}.jsonl"
    reporter = MigrationReporter(path, None)
    value.index = 1
    reporter.emit("execute_recipe", "completed", value, "one", failure_reason="Build failed")
    value.index = 2
    reporter.emit("prepare_step", "started", value, "two")
    reporter.emit("prepare_step", "usage", value, "two", input_tokens=5)
    reporter.close()
    run = JournalCatalog(tmp_path, Path(".refinery")).snapshot(value.run_id).run
    assert run.recipe == RECIPES[1]
    assert run.step_status == "started"
    assert run.failure_reason is None


def test_resumed_recipe_summary_matches_new_parent_visit(tmp_path: Path) -> None:
    from refinery_ai.monitor.recording import InvocationContext, parent_invocation

    value = state(tmp_path)
    plan = report(tmp_path, "plan", RECIPES).plan
    assert isinstance(plan, RecipePlan)
    value.plan.steps = plan.recommendations
    value.index = 1
    path = tmp_path / ".refinery/migration-events" / f"{value.run_id}.jsonl"
    reporter = MigrationReporter(path, None)
    with parent_invocation(InvocationContext("b" * 32, "original")):
        reporter.emit("execute_recipe", "failed", value, "old")
    value.generation += 1
    with parent_invocation(InvocationContext("b" * 32, "resumed")):
        reporter.emit("recover_run", "started", value, "new")
    reporter.close()
    run = JournalCatalog(tmp_path, Path(".refinery")).snapshot(value.run_id).run
    assert run.parent_execution_id == "resumed"
    assert run.recipe is None
