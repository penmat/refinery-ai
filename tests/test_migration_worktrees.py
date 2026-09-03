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
        first, 1, recommendation("recipe.one"), result(), result(), "Java 8 → Java 11"
    )

    assert checkpoint != starting
    assert git(first.path, "log", "-1", "--format=%s").strip() == (
        "Refinery: Java 8 → Java 11"
    )
    assert "migration one" in git(tmp_path, "show", f"{checkpoint}:app.txt")
    assert first.path.is_dir()

    second = manager.create_step(2, checkpoint)
    assert (second.path / "app.txt").read_text() == "migration one\n"
    (second.path / "app.txt").write_text("broken migration two\n")
    reports = second.path / "target/surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-example.xml").write_text("<testsuite failures=\"1\"/>\n")
    generated = second.path / "build/generated.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated output\n")
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
    failure_patch = (artifacts / "changes.patch").read_text()
    assert "broken migration two" in failure_patch
    assert "build/generated.txt" not in failure_patch
    assert (artifacts / "failure-summary.md").is_file()
    assert (artifacts / "surefire-reports/TEST-example.xml").is_file()
    assert git(tmp_path, "status", "--porcelain") == ""

    published = manager.publish(starting, checkpoint)
    assert published == checkpoint
    assert (tmp_path / "app.txt").read_text() == "migration one\n"


def test_worktrees_record_failed_baseline_separately(tmp_path: Path) -> None:
    initialize(tmp_path)
    manager = GitWorktreeManager(tmp_path, Path(".refinery"), "run1", "plan1")
    manager.start()

    artifacts = manager.record_baseline(result(1))

    assert artifacts.name == "baseline"
    assert '"status": "failed"' in (artifacts / "baseline.json").read_text()
    assert "not a migration regression" in (artifacts / "failure-summary.md").read_text()


def test_baseline_build_mutations_are_isolated_from_source_checkout(tmp_path: Path) -> None:
    initialize(tmp_path)
    manager = GitWorktreeManager(tmp_path, Path(".refinery"), "run1", "plan1")
    starting = manager.start()

    baseline = manager.create_baseline(starting)
    (baseline.path / "app.txt").write_text("changed by tests\n")
    manager.rollback(baseline)

    assert (tmp_path / "app.txt").read_text() == "initial\n"
    assert git(tmp_path, "status", "--porcelain") == ""


def test_checkpoint_preserves_patch_with_legacy_encoded_bytes(tmp_path: Path) -> None:
    initialize(tmp_path)
    properties = tmp_path / "application.properties"
    properties.write_bytes("name=Jos\xe9\n".encode("latin-1"))
    git(tmp_path, "add", "application.properties")
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", "legacy properties"],
        cwd=tmp_path,
        env=commit_env,
        check=True,
    )
    manager = GitWorktreeManager(tmp_path, Path(".refinery"), "run1", "plan1")
    starting = manager.start()
    workspace = manager.create_step(1, starting)
    (workspace.path / "application.properties").write_text(
        "name=José\n", encoding="utf-8"
    )

    checkpoint = manager.checkpoint(
        workspace,
        1,
        recommendation("refinery.convert-application-properties-to-utf8"),
        result(),
        result(),
        "Convert application properties to UTF-8",
    )

    assert checkpoint != starting
    patch = (workspace.artifact_directory / "changes.patch").read_bytes()
    assert b"Jos\xe9" in patch
    assert "José".encode() in patch
