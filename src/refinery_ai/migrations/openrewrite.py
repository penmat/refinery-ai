"""OpenRewrite migration implementation."""

from refinery_ai.models import DiscoverySnapshot, RecipeRecommendation, VerificationResult
from refinery_ai.openrewrite import OpenRewriteAdapter
from refinery_ai.recipes.sources import RECIPE_RELEASES


class OpenRewriteMigrationRunner:
    """Execute the recipe selected by one persisted OpenRewrite step."""

    def run(
        self,
        snapshot: DiscoverySnapshot,
        step: RecipeRecommendation,
        *,
        apply: bool,
        timeout: int,
        environment: dict[str, str] | None = None,
    ) -> VerificationResult:
        adapter = OpenRewriteAdapter(allowed_recipes=frozenset({step.recipe}))
        return adapter.run(
            snapshot,
            step.recipe,
            apply=apply,
            timeout=timeout,
            recipe_artifact=step.recipe_artifact or _infer_recipe_artifact(step.recipe),
            environment=environment,
        )


def _infer_recipe_artifact(recipe: str) -> str:
    """Support plans created before artifact coordinates were persisted."""

    spring_namespaces = (
        "org.openrewrite.java.spring.",
        "org.openrewrite.java.springdoc.",
    )
    artifact = "rewrite-spring" if recipe.startswith(spring_namespaces) else "rewrite-migrate-java"
    repository = f"openrewrite/{artifact}"
    return f"org.openrewrite.recipe:{artifact}:{RECIPE_RELEASES[repository]}"
