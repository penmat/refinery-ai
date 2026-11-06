from pathlib import Path

import pytest
from typer.testing import CliRunner

from refinery_ai.cli import app
from refinery_ai.migrations.execution_state import MigrationPolicy, RunManifest
from refinery_ai.migrations.run_store import MigrationRunStore
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    MigrationRun,
    PersistedMigrationPlan,
    RunStatus,
)


@pytest.mark.parametrize("conflict", [False, True])
def test_resume_cli_inherits_publication_intent_and_implies_apply(
    tmp_path: Path, monkeypatch, conflict: bool
) -> None:
    import refinery_ai.cli as cli

    monkeypatch.chdir(tmp_path)
    run_id = "b" * 32
    store = MigrationRunStore(tmp_path, Path(".refinery"), run_id)
    plan = PersistedMigrationPlan(
        plan_id="plan1",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
        steps=[],
    )
    store.create(
        RunManifest(
            run_id=run_id,
            repository=tmp_path,
            state_directory=Path(".refinery"),
            plan=plan,
            starting_revision="abc",
            policy=MigrationPolicy(),
            publish=False,
        )
    )
    captured = []

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            captured.append(kwargs)
            return MigrationRun(plan_id="plan1", status=RunStatus.COMPLETED, run_id=run_id)

    monkeypatch.setattr(cli, "MigrationRunner", Runner)
    args = ["migrate", str(tmp_path), "--resume-run", run_id]
    if conflict:
        args += ["--apply-result"]
    result = CliRunner().invoke(app, args)
    if conflict:
        assert result.exit_code == 2
        assert not captured
    else:
        assert result.exit_code == 0, result.output or repr(result.exception)
        assert captured[0]["apply"] is True
        assert captured[0]["publish"] is False
        assert captured[0]["resume_run_id"] == run_id
