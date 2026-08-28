from pathlib import Path

import pytest

from refinery_ai.models import BuildSystem, DiscoverySnapshot
from refinery_ai.openrewrite import OpenRewriteAdapter


def test_builds_maven_wrapper_dry_run_command(tmp_path: Path) -> None:
    (tmp_path / "mvnw").touch()
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)
    command = OpenRewriteAdapter._command(snapshot, "org.openrewrite.java.migrate.UpgradeToJava17")
    assert command[0] == "./mvnw"
    assert "dryRun" in command[2]
    assert "rewrite-migrate-java" in command[3]


def test_rejects_unknown_build_system(tmp_path: Path) -> None:
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.UNKNOWN)
    with pytest.raises(ValueError, match="Maven or Gradle"):
        OpenRewriteAdapter._command(snapshot, "recipe")


def test_builds_apply_commands(tmp_path: Path) -> None:
    maven = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)
    gradle = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.GRADLE)

    assert OpenRewriteAdapter._command(maven, "recipe", apply=True)[2].endswith(":run")
    assert OpenRewriteAdapter._command(gradle, "recipe", apply=True)[1] == "rewriteRun"


def test_uses_recipe_specific_artifact(tmp_path: Path) -> None:
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)
    command = OpenRewriteAdapter._command(
        snapshot,
        "org.openrewrite.java.springdoc.SpringFoxToSpringDoc",
        recipe_artifact="org.openrewrite.recipe:rewrite-spring:RELEASE",
    )

    assert "rewrite-spring:RELEASE" in command[3]
