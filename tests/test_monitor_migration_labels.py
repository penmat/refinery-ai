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
    reporter.emit("prepare_step", "started", value, "step-two")
    reporter.close()
    event = read_migration_events(path)[0]
    assert (event.migration_name, event.step, event.total_steps) == ("Upgrade to Java 17", 2, 4)
    run = JournalCatalog(tmp_path, Path(".refinery")).snapshot(value.run_id).run
    assert (run.migration_name, run.step, run.total_steps) == ("Upgrade to Java 17", 2, 4)
    # Older journals must still load without new optional fields.
    payload = json.loads(path.read_text())
    for key in ("migration_name", "total_steps", "migrations"):
        payload.pop(key, None)
    path.write_text(json.dumps(payload) + "\n")
    assert read_migration_events(path)[0].recipe == RECIPES[1]
