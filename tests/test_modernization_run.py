import pytest
from test_modernization_models import intent
from test_modernization_progress import evidence

from refinery_ai.config import Settings
from refinery_ai.modernization.runner import ModernizationRunner
from refinery_ai.modernization.store import IntentPlanStore


def test_final_verification_required_even_without_changes(tmp_path):
    plan = intent(tmp_path)
    IntentPlanStore(tmp_path).save(plan)
    runner = ModernizationRunner(
        tmp_path, Settings(), collect=lambda: evidence(tmp_path), verify_final=lambda: False
    )
    result = runner.run(plan.plan_id, apply=True)
    assert result.status != "completed"


def test_external_edits_rejected(tmp_path):
    plan = intent(tmp_path)
    IntentPlanStore(tmp_path).save(plan)
    changed = evidence(tmp_path)
    changed.source_fingerprint = "external"
    runner = ModernizationRunner(
        tmp_path, Settings(), collect=lambda: changed, verify_final=lambda: True
    )
    with pytest.raises(ValueError, match="regenerate"):
        runner.run(plan.plan_id, apply=True)


def test_apply_required(tmp_path):
    runner = ModernizationRunner(tmp_path, Settings())
    with pytest.raises(ValueError, match="apply"):
        runner.run("plan", apply=False)


def test_resume_reuses_pending_child_without_resolving(tmp_path):
    from refinery_ai.models import MigrationRun, RunStatus

    plan = intent(tmp_path)
    IntentPlanStore(tmp_path).save(plan)
    current = evidence(tmp_path, java="17")
    calls = []

    def migrate(child):
        calls.append(child)
        if len(calls) == 1:
            raise RuntimeError("interruption")
        current.module_versions["."]["java"] = "21"
        return MigrationRun(run_id="a" * 32, plan_id=child, status=RunStatus.COMPLETED)

    runner = ModernizationRunner(
        tmp_path, Settings(), collect=lambda: current, verify_final=lambda: True, migrate=migrate
    )
    with pytest.raises(RuntimeError, match="interruption"):
        runner.run(plan.plan_id, apply=True)
    result = runner.run(plan.plan_id, apply=True)
    assert result.status == "completed"
    assert calls[0] == calls[1]


def test_repeated_noop_stops_before_cycle_limit(tmp_path):
    from refinery_ai.models import MigrationRun, RunStatus

    plan = intent(tmp_path)
    IntentPlanStore(tmp_path).save(plan)
    calls = []

    def migrate(child):
        calls.append(child)
        return MigrationRun(run_id="b" * 32, plan_id=child, status=RunStatus.COMPLETED)

    runner = ModernizationRunner(
        tmp_path,
        Settings(),
        collect=lambda: evidence(tmp_path, java="17"),
        verify_final=lambda: True,
        migrate=migrate,
    )
    result = runner.run(plan.plan_id, apply=True, max_cycles=4)
    assert "Repeated" in result.message
    assert len(calls) == 1


def test_resume_rejects_changed_repair_execution_settings(tmp_path):
    plan = intent(tmp_path)
    IntentPlanStore(tmp_path).save(plan)
    first = ModernizationRunner(
        tmp_path, Settings(), collect=lambda: evidence(tmp_path), verify_final=lambda: False
    )
    first.run(plan.plan_id, apply=True)
    changed = Settings(codex_executable="different-codex")
    second = ModernizationRunner(
        tmp_path, changed, collect=lambda: evidence(tmp_path), verify_final=lambda: True
    )
    with pytest.raises(ValueError, match="settings changed"):
        second.run(plan.plan_id, apply=True)


def test_progress_store_rejects_symlink(tmp_path):
    from refinery_ai.modernization.runner import ModernizationRunStore

    state = tmp_path / ".refinery"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "modernization-runs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match=r"symlink|escapes"):
        ModernizationRunStore(tmp_path, Settings().state_directory, "abc")


def test_final_report_includes_new_advisories(tmp_path):
    plan = intent(tmp_path)
    IntentPlanStore(tmp_path).save(plan)
    observed = evidence(tmp_path)
    observed.vulnerabilities = {"pkg:maven/g/a@1": [{"id": "CVE-new"}]}
    runner = ModernizationRunner(
        tmp_path, Settings(), collect=lambda: observed, verify_final=lambda: True
    )
    result = runner.run(plan.plan_id, apply=True)
    assert any("CVE-new" in message for message in result.limitations)


@pytest.mark.parametrize("analysis_cache", [False, True])
def test_resume_after_publication_before_completion_marker(tmp_path, monkeypatch, analysis_cache):
    import subprocess

    from test_migration_runner import initialize_git_repository

    from refinery_ai.migrations.run_store import MigrationRunStore
    from refinery_ai.migrations.runner import MigrationRunner
    from refinery_ai.models import ExecutionType, VerificationResult
    from refinery_ai.modernization.evidence import collect_evidence
    from refinery_ai.modernization.policy import mode_policy
    from refinery_ai.planning_artifacts import MigrationPlanStore

    initialize_git_repository(tmp_path)
    (tmp_path / "pom.xml").write_text(
        "<project><properties><maven.compiler.release>17</maven.compiler.release></properties></project>"
    )
    subprocess.run(["git", "add", "pom.xml"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "pom",
        ],
        cwd=tmp_path,
        check=True,
    )
    settings = Settings()

    def collect():
        return collect_evidence(tmp_path, mode_policy("minimal"), settings)

    if analysis_cache:
        (tmp_path / ".codegraph").mkdir()
        (tmp_path / ".codegraph/index.db").write_text("analysis fixture")
    initial = collect()
    plan = intent(
        tmp_path,
        mode="minimal",
        source_revision=initial.revision,
        build_fingerprint=initial.build_fingerprint,
        source_fingerprint=initial.source_fingerprint,
    )
    IntentPlanStore(tmp_path).save(plan)
    calls = []

    class Recipes:
        def run(self, snapshot, step, **kwargs):
            calls.append(step.recipe)
            path = snapshot.repository / "pom.xml"
            path.write_text(path.read_text().replace("17", "21"))
            return VerificationResult(command=["fixture"], exit_code=0, duration_seconds=0)

    child = MigrationRunner(
        MigrationPlanStore(tmp_path),
        runners={ExecutionType.OPENREWRITE: Recipes()},
        build_verifier=lambda *args, **kwargs: VerificationResult(
            command=["test"], exit_code=0, duration_seconds=0
        ),
    )
    original = MigrationRunStore.save_publication

    def crash(store, publication):
        if publication.completed:
            raise KeyboardInterrupt("publication marker")
        original(store, publication)

    monkeypatch.setattr(MigrationRunStore, "save_publication", crash)
    runner = ModernizationRunner(
        tmp_path,
        settings,
        collect=collect,
        verify_final=lambda: True,
        migrate=lambda plan_id: child.run(plan_id, apply=True, publish=True, resume_bound_run=True),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run(plan.plan_id, apply=True)
    monkeypatch.setattr(MigrationRunStore, "save_publication", original)
    result = runner.run(plan.plan_id, apply=True)
    assert result.status == "completed"
    assert len(calls) == 1
