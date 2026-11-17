"""Bounded options and stable identity for inspected dependency transformations."""

import hashlib
import json
import re

from refinery_ai.models import RecipeRecommendation
from refinery_ai.modernization.models import exact_version

DEPENDENCY_RECIPES = frozenset(
    {
        "org.openrewrite.maven.UpgradeDependencyVersion",
        "org.openrewrite.gradle.UpgradeDependencyVersion",
        "org.openrewrite.maven.UpgradeParentVersion",
    }
)


def validate_options(recipe: str, options: dict[str, str | int | bool]) -> None:
    if recipe == "refinery.UpgradeSpringBoot":
        if set(options) != {"groupId", "artifactId", "newVersion", "migrateJakarta"}:
            raise ValueError("Unsupported framework options")
        if (
            options["groupId"] != "org.springframework.boot"
            or options["artifactId"]
            not in {"spring-boot-starter-parent", "spring-boot-dependencies"}
            or not isinstance(options["migrateJakarta"], bool)
        ):
            raise ValueError("Unsupported framework owner")
        validate_options(
            "org.openrewrite.maven.UpgradeDependencyVersion",
            {k: v for k, v in options.items() if k != "migrateJakarta"},
        )
        return
    if not options and recipe not in DEPENDENCY_RECIPES:
        return
    if recipe not in DEPENDENCY_RECIPES or set(options) != {"groupId", "artifactId", "newVersion"}:
        raise ValueError("Unsupported recipe or option keys")
    for name in ("groupId", "artifactId"):
        value = options[name]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("Exact dependency coordinates are required")
    value = options["newVersion"]
    if not isinstance(value, str):
        raise ValueError("Exact target version required")
    exact_version(value)


def execution_identity(step: RecipeRecommendation) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "recipe": step.recipe,
                "artifact": step.recipe_artifact,
                "options": step.options,
                "objective_id": step.objective_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def recipe_entries(
    recipe: str, options: dict[str, str | int | bool]
) -> list[dict[str, dict[str, str | int | bool]]]:
    """Build the bounded, atomic wrapper for a saved transformation."""
    validate_options(recipe, options)
    if recipe != "refinery.UpgradeSpringBoot":
        return [{recipe: options}]
    pin = (
        "org.openrewrite.maven.UpgradeParentVersion"
        if options["artifactId"] == "spring-boot-starter-parent"
        else "org.openrewrite.maven.UpgradeDependencyVersion"
    )
    entries = [{pin: {k: v for k, v in options.items() if k != "migrateJakarta"}}]
    if options["migrateJakarta"]:
        for package in (
            "persistence",
            "servlet",
            "validation",
            "annotation",
            "transaction",
            "inject",
            "activation",
            "xml.bind",
        ):
            entries.append(
                {
                    "org.openrewrite.java.ChangePackage": {
                        "oldPackageName": f"javax.{package}",
                        "newPackageName": f"jakarta.{package}",
                        "recursive": package not in {"annotation", "transaction"},
                    }
                }
            )
    return entries
