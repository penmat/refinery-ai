"""Review-gated OpenRewrite command adapter."""

from __future__ import annotations

from pathlib import Path

from refinery_ai.models import BuildSystem, DiscoverySnapshot, VerificationResult
from refinery_ai.verification import verify


class OpenRewriteAdapter:
    """Construct and run allowlisted OpenRewrite dry-run commands."""

    MAVEN_PLUGIN = "org.openrewrite.maven:rewrite-maven-plugin:6.46.1:dryRun"
    JAVA_RECIPE_ARTIFACT = "org.openrewrite.recipe:rewrite-migrate-java:RELEASE"

    def __init__(self, allowed_recipes: frozenset[str] | None = None) -> None:
        self.allowed_recipes = allowed_recipes or frozenset(
            {
                "org.openrewrite.java.migrate.UpgradeToJava8",
                "org.openrewrite.java.migrate.UpgradeToJava11",
                "org.openrewrite.java.migrate.UpgradeToJava17",
                "org.openrewrite.java.migrate.UpgradeToJava21",
            }
        )

    def dry_run(
        self, snapshot: DiscoverySnapshot, recipe: str, *, timeout: int = 900
    ) -> VerificationResult:
        if recipe not in self.allowed_recipes:
            raise ValueError(f"Recipe is not allowlisted: {recipe}")
        command = self._command(snapshot, recipe)
        return verify(snapshot.repository, command, timeout=timeout)

    @staticmethod
    def _command(snapshot: DiscoverySnapshot, recipe: str) -> list[str]:
        root: Path = snapshot.repository
        if snapshot.build_system is BuildSystem.MAVEN:
            executable = "./mvnw" if (root / "mvnw").exists() else "mvn"
            return [
                executable,
                "-U",
                OpenRewriteAdapter.MAVEN_PLUGIN,
                (f"-Drewrite.recipeArtifactCoordinates={OpenRewriteAdapter.JAVA_RECIPE_ARTIFACT}"),
                f"-Drewrite.activeRecipes={recipe}",
                "-Drewrite.exportDatatables=true",
            ]
        if snapshot.build_system is BuildSystem.GRADLE:
            executable = "./gradlew" if (root / "gradlew").exists() else "gradle"
            return [executable, "rewriteDryRun", f"-Drewrite.activeRecipe={recipe}"]
        raise ValueError("OpenRewrite requires a Maven or Gradle repository")
