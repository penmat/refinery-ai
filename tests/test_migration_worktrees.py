import os
import subprocess
from pathlib import Path

from refinery_ai.migrations.worktrees import GitWorktreeManager
from refinery_ai.models import RecipeRecommendation, VerificationResult


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def initialize(repository: Path) -> None:
    git(repository, "init", "-q")
    (repository / ".gitignore").write_text(".refinery/\ntarget/\n")
    (repository / "app.txt").write_text("initial\n")
    git(repository, "add", ".gitignore", "app.txt")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"],
        cwd=repository,
        env=env,
        check=True,
    )


def recommendation(name: str) -> RecipeRecommendation:
    return RecipeRecommendation(
        recipe=name,
        reason="Test recipe.",
        evidence=["fixture"],
        expected_benefit="Test benefit.",
        priority="high",
        confidence=1,
    )


def result(exit_code: int = 0) -> VerificationResult:
    return VerificationResult(command=["test"], exit_code=exit_code, duration_seconds=0)


def test_worktrees_chain_green_checkpoint_and_rollback_only_failed_step(
    tmp_path: Path,
) -> None:
    initialize(tmp_path)
    manager = GitWorktreeManager(tmp_path, Path(".refinery"), "run1", "plan1")
    starting = manager.start()

    first = manager.create_step(1, starting)
    (first.path / "app.txt").write_text("migration one\n")
    checkpoint = manager.checkpoint(
        first, 1, recommendation("recipe.one"), result(), result()
    )

    assert checkpoint != starting
    assert "migration one" in git(tmp_path, "show", f"{checkpoint}:app.txt")
    assert first.path.is_dir()

    second = manager.create_step(2, checkpoint)
    assert (second.path / "app.txt").read_text() == "migration one\n"
    (second.path / "app.txt").write_text("broken migration two\n")
    artifacts = manager.record_failure(
        second,
        2,
        recommendation("recipe.two"),
        "Post-migration build failed",
        result(),
        result(1),
    )

    assert not second.path.exists()
    assert first.path.is_dir()
    assert "broken migration two" in (artifacts / "changes.patch").read_text()
    assert (artifacts / "failure-summary.md").is_file()
    assert git(tmp_path, "status", "--porcelain") == ""

    published = manager.publish(starting, checkpoint)
    assert published == checkpoint
    assert (tmp_path / "app.txt").read_text() == "migration one\n"
