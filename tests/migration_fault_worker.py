"""Test-only process killed at real persistence boundaries."""

import os
import signal
import sys
from pathlib import Path

from refinery_ai.migrations.run_store import MigrationRunStore
from refinery_ai.migrations.runner import MigrationRunner
from refinery_ai.migrations.worktrees import GitWorktreeManager
from refinery_ai.models import ExecutionType, VerificationResult
from refinery_ai.planning_artifacts import MigrationPlanStore

root = Path(sys.argv[1])
fault = sys.argv[2]
resume = sys.argv[3] if len(sys.argv) > 3 else None
calls = root.parent / "calls"


def append(path, value):
    with path.open("a") as output:
        output.write(value + "\n")
        output.flush()
        os.fsync(output.fileno())


def crash():
    os.kill(os.getpid(), signal.SIGKILL)


class Recipes:
    def run(self, snapshot, step, **kwargs):
        append(calls, step.recipe)
        path = snapshot.repository / "change"
        path.write_text((path.read_text() if path.exists() else "") + step.recipe + "\n")
        return VerificationResult(command=["recipe"], exit_code=0, duration_seconds=0)


def verify(repository, command, **kwargs):
    result = VerificationResult(command=["test"], exit_code=0, duration_seconds=0)
    if fault == "verified" and repository.name.startswith("step-001"):
        crash()
    return result


original_checkpoint = GitWorktreeManager.checkpoint


def checkpoint(self, workspace, index, *args, **kwargs):
    result = original_checkpoint(self, workspace, index, *args, **kwargs)
    if fault == "commit" and index == 1:
        crash()
    return result


GitWorktreeManager.checkpoint = checkpoint
original_receipt = MigrationRunStore.save_step


def receipt(self, value):
    original_receipt(self, value)
    if fault == "receipt" and value.step == 1:
        crash()


MigrationRunStore.save_step = receipt
original_publish = GitWorktreeManager.publish


def publish(self, *args, **kwargs):
    result = original_publish(self, *args, **kwargs)
    append(root.parent / "publications", "published")
    if fault == "published":
        crash()
    return result


GitWorktreeManager.publish = publish
original_result = MigrationRunStore.save_result


def save_result(self, value):
    original_result(self, value)
    if fault == "result":
        crash()


MigrationRunStore.save_result = save_result
runner = MigrationRunner(
    MigrationPlanStore(root), runners={ExecutionType.OPENREWRITE: Recipes()}, build_verifier=verify
)
result = runner.run(apply=True, resume_run_id=resume)
(root.parent / "result.json").write_text(result.model_dump_json())
