"""Git worktree isolation and checkpoint artifacts for migration steps."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from refinery_ai.logging import get_logger, safe_command
from refinery_ai.models import RecipeRecommendation, VerificationResult

logger = get_logger(__name__)


class MigrationWorkspaceError(RuntimeError):
    """Raised when an isolated migration workspace cannot be prepared safely."""


@dataclass(frozen=True)
class StepWorkspace:
    path: Path
    artifact_directory: Path
    base_revision: str


class GitWorktreeManager:
    """Create one isolated worktree per recipe and retain verified checkpoints."""

    def __init__(
        self,
        repository: Path,
        state_directory: Path,
        run_id: str,
        plan_id: str,
    ) -> None:
        self.repository = repository.expanduser().resolve()
        self.run_id = run_id
        self.plan_id = plan_id
        state_root = self.repository / state_directory
        self.worktree_root = state_root / "worktrees" / run_id
        self.artifact_root = state_root / "migration-runs" / run_id

    def start(self) -> str:
        """Validate the source checkout and persist its immutable starting revision."""

        self._ensure_clean()
        revision = self._git(self.repository, "rev-parse", "HEAD").strip()
        self.artifact_root.mkdir(parents=True, exist_ok=False)
        self._write_json(
            self.artifact_root / "run.json",
            {
                "run_id": self.run_id,
                "plan_id": self.plan_id,
                "repository": str(self.repository),
                "starting_revision": revision,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        return revision

    def create_step(self, index: int, base_revision: str) -> StepWorkspace:
        stem = f"step-{index:03d}"
        path = self.worktree_root / stem
        artifacts = self.artifact_root / stem
        artifacts.mkdir(parents=True, exist_ok=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(self.repository, "worktree", "add", "--detach", str(path), base_revision)
        return StepWorkspace(path=path, artifact_directory=artifacts, base_revision=base_revision)

    def record_baseline(self, verification: VerificationResult) -> Path:
        """Persist the build result from the untouched source revision."""

        artifacts = self.artifact_root / "baseline"
        artifacts.mkdir(parents=True, exist_ok=False)
        self._write_json(
            artifacts / "baseline.json",
            {
                "status": "completed" if verification.green else "failed",
                "verification": verification.model_dump(mode="json"),
            },
        )
        self._write_verification(artifacts / "build.log", verification)
        if not verification.green:
            (artifacts / "failure-summary.md").write_text(
                "# Failed baseline build\n\n"
                "The unchanged repository did not pass verification. No migration recipe "
                "was executed, so this is not a migration regression.\n"
            )
        return artifacts

    def checkpoint(
        self,
        workspace: StepWorkspace,
        index: int,
        step: RecipeRecommendation,
        execution: VerificationResult,
        verification: VerificationResult,
        title: str | None = None,
    ) -> str:
        """Commit a green step in its isolated worktree and persist its exact patch."""

        self._git(workspace.path, "add", "-A")
        patch = self._git(workspace.path, "diff", "--cached", "--binary", workspace.base_revision)
        changed = self._git_result(workspace.path, "diff", "--cached", "--quiet")
        if changed.returncode == 0:
            revision = workspace.base_revision
        else:
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Refinery AI",
                "GIT_AUTHOR_EMAIL": "refinery@localhost",
                "GIT_COMMITTER_NAME": "Refinery AI",
                "GIT_COMMITTER_EMAIL": "refinery@localhost",
            }
            self._git(
                workspace.path,
                "commit",
                "--no-verify",
                "-m",
                f"Refinery: {title or step.recipe.rsplit('.', maxsplit=1)[-1]}",
                env=env,
            )
            revision = self._git(workspace.path, "rev-parse", "HEAD").strip()
        self._git(
            self.repository,
            "update-ref",
            f"refs/refinery/{self.run_id}/step-{index:03d}",
            revision,
        )
        (workspace.artifact_directory / "changes.patch").write_text(
            patch, errors="surrogateescape"
        )
        self._write_step_metadata(
            workspace,
            index,
            step,
            "completed",
            execution,
            verification,
            checkpoint=revision,
        )
        return revision

    def record_failure(
        self,
        workspace: StepWorkspace,
        index: int,
        step: RecipeRecommendation,
        reason: str,
        execution: VerificationResult | None,
        verification: VerificationResult | None,
    ) -> Path:
        """Capture the failing step before safely removing only its owned worktree."""

        self._git(workspace.path, "add", "-A")
        patch = self._git(workspace.path, "diff", "--cached", "--binary", workspace.base_revision)
        (workspace.artifact_directory / "changes.patch").write_text(
            patch, errors="surrogateescape"
        )
        self._write_step_metadata(
            workspace,
            index,
            step,
            "failed",
            execution,
            verification,
            reason=reason,
        )
        summary = [
            f"# Failed migration {index}",
            "",
            f"**Recipe:** `{step.recipe}`",
            f"**Base revision:** `{workspace.base_revision}`",
            f"**Reason:** {reason}",
            "",
            "The failing worktree was removed after these diagnostics were captured.",
        ]
        (workspace.artifact_directory / "failure-summary.md").write_text(
            "\n".join(summary) + "\n"
        )
        self.rollback(workspace)
        return workspace.artifact_directory

    def rollback(self, workspace: StepWorkspace) -> None:
        """Remove a failing Refinery-owned worktree without touching earlier checkpoints."""

        self._git(self.repository, "worktree", "remove", "--force", str(workspace.path))
        self._git(self.repository, "worktree", "prune")

    def publish(self, starting_revision: str, final_revision: str) -> str:
        """Fast-forward the source checkout to the last verified checkpoint."""

        current = self._git(self.repository, "rev-parse", "HEAD").strip()
        if current != starting_revision:
            raise MigrationWorkspaceError(
                "Source checkout moved during migration; refusing to publish over "
                f"{current} (expected {starting_revision})"
            )
        if final_revision != starting_revision:
            self._git(self.repository, "merge", "--ff-only", final_revision)
        return self._git(self.repository, "rev-parse", "HEAD").strip()

    def finish(
        self,
        status: str,
        final_revision: str,
        published_revision: str | None = None,
    ) -> None:
        """Complete the durable run record after orchestration stops."""

        path = self.artifact_root / "run.json"
        payload = json.loads(path.read_text())
        payload.update(
            {
                "status": status,
                "final_revision": final_revision,
                "published_revision": published_revision,
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        self._write_json(path, payload)

    def _write_step_metadata(
        self,
        workspace: StepWorkspace,
        index: int,
        step: RecipeRecommendation,
        status: str,
        execution: VerificationResult | None,
        verification: VerificationResult | None,
        *,
        checkpoint: str | None = None,
        reason: str | None = None,
    ) -> None:
        payload = {
            "step": index,
            "status": status,
            "recipe": step.model_dump(mode="json"),
            "base_revision": workspace.base_revision,
            "checkpoint": checkpoint,
            "workspace": str(workspace.path),
            "reason": reason,
            "execution": execution.model_dump(mode="json") if execution else None,
            "verification": verification.model_dump(mode="json") if verification else None,
        }
        self._write_json(workspace.artifact_directory / "step.json", payload)
        if execution:
            self._write_verification(workspace.artifact_directory / "execution.log", execution)
        if verification:
            self._write_verification(workspace.artifact_directory / "build.log", verification)

    def _ensure_clean(self) -> None:
        result = self._git(
            self.repository,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        entries = [entry for entry in result.split("\0") if entry]
        dirty = []
        for entry in entries:
            status = entry[:2]
            path = entry[3:]
            if status == "??" and path.startswith((".refinery/", "graphify-out/")):
                continue
            dirty.append(path)
        if dirty:
            preview = ", ".join(dirty[:5])
            raise MigrationWorkspaceError(
                "Migration apply requires a clean source checkout; dirty paths: " + preview
            )

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, indent=2) + "\n")

    @staticmethod
    def _write_verification(path: Path, result: VerificationResult) -> None:
        command = safe_command(result.command)
        path.write_text(
            f"command: {command}\nexit_code: {result.exit_code}\n"
            f"duration_seconds: {result.duration_seconds:.3f}\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}\n"
        )

    @staticmethod
    def _git(
        cwd: Path,
        *arguments: str,
        env: dict[str, str] | None = None,
    ) -> str:
        result = GitWorktreeManager._git_result(cwd, *arguments, env=env)
        if result.returncode != 0:
            command = ["git", *arguments]
            raise MigrationWorkspaceError(
                f"Git workspace command failed ({safe_command(command)}): {result.stderr.strip()}"
            )
        return result.stdout

    @staticmethod
    def _git_result(
        cwd: Path,
        *arguments: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", *arguments]
        logger.debug("Running command in %s: %s", cwd, safe_command(command))
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            capture_output=True,
            check=False,
        )
        return result
