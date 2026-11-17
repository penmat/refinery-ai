"""Deterministic boundaries around model-selected destinations and batches."""

import re

from refinery_ai.models import RecipePlan
from refinery_ai.modernization.models import (
    ModernizationIntentPlan,
    TransformationSpec,
    exact_version,
)
from refinery_ai.modernization.policy import mode_policy
from refinery_ai.modernization.transformations import DEPENDENCY_RECIPES, validate_options
from refinery_ai.recipes.catalog import RecipeCatalogSession

# Compiled, source-only transformations whose minimum JDK and effects are audited.
SOURCE_RECIPES = {
    "org.openrewrite.java.migrate.lang.UseTextBlocks": 17,
    "org.openrewrite.java.migrate.util.UseListOf": 11,
}


def validate_transformation_scope(
    step: TransformationSpec, mode: str, catalog: RecipeCatalogSession
) -> None:
    if not set(step.effects) <= mode_policy(mode).categories:
        raise ValueError("Transformation exceeds mode scope")
    if len(step.artifact.split(":")) != 3:
        raise ValueError("Concrete recipe artifact required")
    exact_version(step.artifact.rsplit(":", 1)[1])
    validate_options(step.recipe, step.options)
    if step.recipe not in catalog.inspected_recipes:
        raise ValueError(f"Recipe composition not inspected: {step.recipe}")
    if catalog.get(step.recipe) is None:
        raise ValueError("Recipe absent from inspected catalog")
    if catalog.recipe_artifacts[step.recipe] != step.artifact:
        raise ValueError("Recipe artifact differs from inspected catalog artifact")

    def inspect_versions(recipe: str, trail: frozenset[str]) -> None:
        if recipe in trail:
            raise ValueError("Recipe composition cycle")
        item = catalog.get(recipe)
        if item is None:
            return
        for child in item.recipe_list:
            if isinstance(child, str):
                inspect_versions(child, trail | {recipe})
            else:
                for name, options in child.items():
                    if isinstance(options, dict):
                        for key, value in options.items():
                            if key.lower() in {"newversion", "version", "gradleversion"}:
                                exact_version(str(value))
                    inspect_versions(name, trail | {recipe})

    inspect_versions(step.recipe, frozenset())
    if mode == "minimal" and step.recipe != "refinery.UpgradeSpringBoot":
        if not re.fullmatch(r"org\.openrewrite\.java\.migrate\.UpgradeBuildToJava\d+", step.recipe):
            raise ValueError("Minimal requires a verified build-only transformation")

        def inspect(recipe: str, trail: frozenset[str]) -> None:
            if recipe in trail:
                raise ValueError("Recipe composition cycle")
            item = catalog.get(recipe)
            if item is None or not item.recipe_list:
                if recipe != "org.openrewrite.java.migrate.UpgradeJavaVersion":
                    raise ValueError(f"Unknown minimal recipe effects: {recipe}")
                return
            for child in item.recipe_list:
                name = child if isinstance(child, str) else next(iter(child))
                inspect(name, trail | {recipe})

        inspect(step.recipe, frozenset())


def validate_intent(plan: ModernizationIntentPlan, catalog: RecipeCatalogSession) -> None:
    ModernizationIntentPlan.model_validate(plan.model_dump())
    for objective in plan.objectives:
        if objective.disposition == "advisory":
            continue
        for step in objective.transformations:
            validate_transformation_scope(step, plan.mode, catalog)
            match = re.search(r"Upgrade(?:Build)?ToJava(\d+)", step.recipe)
            if match and int(match[1]) > plan.requested_jdk:
                raise ValueError("Recipe raises Java beyond saved target")
            if step.recipe in DEPENDENCY_RECIPES or step.recipe == "refinery.UpgradeSpringBoot":
                target = objective.target
                framework_pin = (
                    target is not None
                    and target.component == "spring_boot"
                    and step.options.get("groupId") == "org.springframework.boot"
                    and step.options.get("artifactId")
                    in {"spring-boot-starter-parent", "spring-boot-dependencies"}
                    and step.options.get("newVersion") == target.version
                )
                if not framework_pin and (
                    target is None
                    or step.options
                    != {
                        "groupId": target.group,
                        "artifactId": target.artifact,
                        "newVersion": target.version,
                    }
                ):
                    raise ValueError("Dependency options differ from saved target")


def validate_batch(batch: RecipePlan, intent: ModernizationIntentPlan) -> None:
    objectives = {o.id: o for o in intent.objectives if o.disposition == "executable"}
    for step in batch.recommendations:
        objective = objectives.get(step.objective_id or "")
        if objective is None or not any(
            t.recipe == step.recipe
            and t.options == step.options
            and t.artifact == step.recipe_artifact
            for t in objective.transformations
        ):
            raise ValueError("Batch transformation is outside the saved destination")
