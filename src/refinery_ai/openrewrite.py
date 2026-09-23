"""Review-gated OpenRewrite command adapter."""

from __future__ import annotations

from pathlib import Path

from refinery_ai.build_tools import maven_command_prefix
from refinery_ai.models import BuildSystem, DiscoverySnapshot, VerificationResult
from refinery_ai.verification import verify


class OpenRewriteAdapter:
    """Construct and run allowlisted OpenRewrite commands."""

    MAVEN_PLUGIN = "org.openrewrite.maven:rewrite-maven-plugin:6.46.1:dryRun"
    JAVA_RECIPE_ARTIFACT = "org.openrewrite.recipe:rewrite-migrate-java:3.42.1"

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
        self,
        snapshot: DiscoverySnapshot,
        recipe: str,
        *,
        timeout: int = 900,
        environment: dict[str, str] | None = None,
    ) -> VerificationResult:
        if recipe not in self.allowed_recipes:
            raise ValueError(f"Recipe is not allowlisted: {recipe}")
        command = self._command(snapshot, recipe)
        return verify(snapshot.repository, command, timeout=timeout, environment=environment)

    def run(
        self,
        snapshot: DiscoverySnapshot,
        recipe: str,
        *,
        apply: bool = False,
        timeout: int = 900,
        recipe_artifact: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> VerificationResult:
        """Run a recipe in preview mode unless applying was explicitly requested."""

        if recipe not in self.allowed_recipes:
            raise ValueError(f"Recipe is not allowlisted: {recipe}")
        command = self._command(
            snapshot, recipe, apply=apply, recipe_artifact=recipe_artifact
        )
        return verify(snapshot.repository, command, timeout=timeout, environment=environment)

    @staticmethod
    def _command(
        snapshot: DiscoverySnapshot,
        recipe: str,
        *,
        apply: bool = False,
        recipe_artifact: str | None = None,
    ) -> list[str]:
        root: Path = snapshot.repository
        if snapshot.build_system is BuildSystem.MAVEN:
            goal = OpenRewriteAdapter.MAVEN_PLUGIN
            if apply:
                goal = goal.removesuffix(":dryRun") + ":run"
            return [
                *maven_command_prefix(root),
                "-U",
                goal,
                "-Drewrite.recipeArtifactCoordinates="
                f"{recipe_artifact or OpenRewriteAdapter.JAVA_RECIPE_ARTIFACT}",
                f"-Drewrite.activeRecipes={recipe}",
                "-Drewrite.exportDatatables=true",
            ]
        if snapshot.build_system is BuildSystem.GRADLE:
            executable = "./gradlew" if (root / "gradlew").exists() else "gradle"
            task = "rewriteRun" if apply else "rewriteDryRun"
            return [executable, task, f"-Drewrite.activeRecipe={recipe}"]
        raise ValueError("OpenRewrite requires a Maven or Gradle repository")
