from pathlib import Path

import pytest
from test_migration_runner import initialize_git_repository

from refinery_ai.migrations.runner import MigrationRunner
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    ExecutionType,
    PersistedMigrationPlan,
    RecipeRecommendation,
    VerificationResult,
)
from refinery_ai.planning_artifacts import MigrationPlanStore


def setup_run(root: Path):
    initialize_git_repository(root)
    store = MigrationPlanStore(root)
    store.root.mkdir(parents=True)
    steps = [
        RecipeRecommendation(
            recipe=name,
            reason="test",
            evidence=["test"],
            expected_benefit="test",
            priority="high",
            confidence=1,
        )
        for name in ["first", "second"]
    ]
    plan = PersistedMigrationPlan(
        plan_id="plan1",
        snapshot=DiscoverySnapshot(repository=root, build_system=BuildSystem.MAVEN),
        steps=steps,
    )
    (store.root / "plan1.json").write_text(plan.model_dump_json())
    return store


def test_resume_preserves_verified_recipe_and_restarts_only_interrupted(tmp_path: Path) -> None:
    store = setup_run(tmp_path)
    calls = []
    interrupt = True

    class Recipes:
        def run(self, snapshot, step, **kwargs):
            nonlocal interrupt
            calls.append(step.recipe)
            (snapshot.repository / "change").write_text(step.recipe)
            if step.recipe == "second" and interrupt:
                interrupt = False
                (snapshot.repository / "partial").write_text("unverified")
                raise KeyboardInterrupt
            assert not (snapshot.repository / "partial").exists()
            return VerificationResult(command=["recipe"], exit_code=0, duration_seconds=0)

    runner = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: Recipes()},
        build_verifier=lambda *args, **kwargs: VerificationResult(
            command=["test"], exit_code=0, duration_seconds=0
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run(apply=True)
    run_id = next((tmp_path / ".refinery/migration-runs").iterdir()).name
    result = runner.run(resume_run_id=run_id)
    assert calls == ["first", "second", "second"]
    assert result.status.value == "completed"
    assert result.run_id == run_id
    assert (tmp_path / "change").read_text() == "second"
    assert not (tmp_path / "partial").exists()
    replay = runner.run(resume_run_id=run_id)
    assert replay == result
    assert calls == ["first", "second", "second"]


@pytest.mark.parametrize("fault", ["verified", "commit", "receipt", "published", "result"])
def test_real_process_crash_reconciles_external_effects(tmp_path: Path, fault: str) -> None:
    import signal
    import subprocess
    import sys

    root = tmp_path / "repository"
    root.mkdir()
    setup_run(root)
    worker = Path(__file__).with_name("migration_fault_worker.py")
    stopped = subprocess.run(
        [sys.executable, str(worker), str(root), fault], capture_output=True, text=True, timeout=20
    )
    assert stopped.returncode == -signal.SIGKILL, stopped.stderr
    run_id = next((root / ".refinery/migration-runs").iterdir()).name
    completed = subprocess.run(
        [sys.executable, str(worker), str(root), "none", run_id],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert (root / "change").read_text() == "first\nsecond\n"
    expected = (
        ["first", "first", "second"] if fault in ("verified", "commit") else ["first", "second"]
    )
    assert (tmp_path / "calls").read_text().splitlines() == expected
    assert (tmp_path / "publications").read_text().splitlines() == ["published"]


def interrupted_run(root: Path):
    store = setup_run(root)

    class Recipes:
        def run(self, snapshot, step, **kwargs):
            (snapshot.repository / "change").write_text(step.recipe)
            if step.recipe == "second":
                raise KeyboardInterrupt
            return VerificationResult(command=["recipe"], exit_code=0, duration_seconds=0)

    runner = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: Recipes()},
        build_verifier=lambda *a, **kw: VerificationResult(
            command=["test"], exit_code=0, duration_seconds=0
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run(apply=True)
    run_id = next((root / ".refinery/migration-runs").iterdir()).name
    return runner, run_id


@pytest.mark.parametrize(
    "problem",
    ["dirty", "moved", "plan", "receipt", "missing", "ref", "schema", "policy", "terminal"],
)
def test_unsafe_resume_refuses_without_deleting_owned_work(tmp_path: Path, problem: str) -> None:
    import json
    import subprocess

    from refinery_ai.migrations.execution_state import MigrationPolicy
    from refinery_ai.migrations.run_store import MigrationRunStore

    runner, run_id = interrupted_run(tmp_path)
    store = MigrationRunStore(tmp_path, Path(".refinery"), run_id)
    if problem == "dirty":
        (tmp_path / "unrelated").write_text("keep")
    elif problem == "moved":
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-qm",
                "moved",
            ],
            cwd=tmp_path,
            check=True,
        )
    elif problem == "plan":
        path = runner.store.root / "plan1.json"
        payload = json.loads(path.read_text())
        payload["steps"][0]["reason"] = "changed"
        path.write_text(json.dumps(payload))
    elif problem == "receipt":
        path = store.path("step-001/step.json")
        payload = json.loads(path.read_text())
        payload["verification"]["exit_code"] = 1
        path.write_text(json.dumps(payload))
    elif problem == "missing":
        store.path("step-001/step.json").unlink()
    elif problem == "ref":
        subprocess.run(
            ["git", "update-ref", f"refs/refinery/{run_id}/step-001", "HEAD"],
            cwd=tmp_path,
            check=True,
        )
    elif problem == "schema":
        path = store.path("manifest.json")
        payload = json.loads(path.read_text())
        payload["schema_version"] = 99
        path.write_text(json.dumps(payload))
    elif problem == "policy":
        runner.policy = MigrationPolicy(timeout=123)
    elif problem == "terminal":
        store.path("step-002/step.json").write_text('{"status":"failed"}')
    before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path)
    workspace = tmp_path / ".refinery/worktrees" / run_id / "step-002"
    with pytest.raises((ValueError, RuntimeError)):
        runner.run(resume_run_id=run_id)
    assert workspace.exists()
    assert (workspace / "change").read_text() == "second"
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path) == before
