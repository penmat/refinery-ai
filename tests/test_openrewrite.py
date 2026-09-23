from pathlib import Path

import pytest

from refinery_ai.models import BuildSystem, DiscoverySnapshot
from refinery_ai.openrewrite import OpenRewriteAdapter


def test_builds_non_executable_maven_wrapper_dry_run_command(tmp_path: Path) -> None:
    (tmp_path / "mvnw").touch()
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)
    command = OpenRewriteAdapter._command(snapshot, "org.openrewrite.java.migrate.UpgradeToJava17")
    assert command[:2] == ["sh", "./mvnw"]
    assert any("dryRun" in argument for argument in command)
    assert any("rewrite-migrate-java" in argument for argument in command)


def test_rejects_unknown_build_system(tmp_path: Path) -> None:
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.UNKNOWN)
    with pytest.raises(ValueError, match="Maven or Gradle"):
        OpenRewriteAdapter._command(snapshot, "recipe")


def test_builds_apply_commands(tmp_path: Path) -> None:
    maven = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)
    gradle = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.GRADLE)

    assert any(
        argument.endswith(":run")
        for argument in OpenRewriteAdapter._command(maven, "recipe", apply=True)
    )
    assert OpenRewriteAdapter._command(gradle, "recipe", apply=True)[1] == "rewriteRun"


def test_uses_recipe_specific_artifact(tmp_path: Path) -> None:
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)
    command = OpenRewriteAdapter._command(
        snapshot,
        "org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        recipe_artifact="org.openrewrite.recipe:rewrite-spring:6.37.1",
    )

    assert any("rewrite-spring:6.37.1" in argument for argument in command)
