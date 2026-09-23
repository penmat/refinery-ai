"""Versioned recipe semantics and deterministic constraint scheduling."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from refinery_ai.models import RecipePlan, RecipePriority, RecipeRecommendation
from refinery_ai.recipes.sources import RecipeSource

KNOWLEDGE_SCHEMA_VERSION = 1


class RecipeEffect(BaseModel):
    property: str
    operation: str
    value: str | int
    introduced_by: str
    path: list[str] = Field(default_factory=list)


class ResolvedRecipeKnowledge(BaseModel):
    recipe: str
    repository: str | None = None
    source_revision: str | None = None
    artifact: str | None = None
    direct_children: list[str] = Field(default_factory=list)
    effects: list[RecipeEffect] = Field(default_factory=list)


class RecipeKnowledgeSnapshot(BaseModel):
    schema_version: int = KNOWLEDGE_SCHEMA_VERSION
    catalog_fingerprint: str
    semantic_rules_fingerprint: str
    artifacts: list[str] = Field(default_factory=list)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    recipes: dict[str, ResolvedRecipeKnowledge] = Field(default_factory=dict)

    def persist(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(self.model_dump_json(indent=2) + "\n")
        temporary.replace(path)


def load_semantic_rules() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "recipe-knowledge" / "semantics.yaml"
    payload = yaml.safe_load(path.read_text())
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"Invalid recipe semantics document: {path}")
    return payload


def build_recipe_knowledge(
    sources: list[RecipeSource], rules: dict[str, Any] | None = None
) -> RecipeKnowledgeSnapshot:
    """Resolve recursive compositions and retain the provenance of every effect."""

    semantic_rules = rules or load_semantic_rules()
    by_name = {source.recipe: source for source in sources}
    resolved: dict[str, ResolvedRecipeKnowledge] = {}

    def resolve(recipe: str, trail: tuple[str, ...] = ()) -> ResolvedRecipeKnowledge:
        if recipe in resolved:
            return resolved[recipe]
        if recipe in trail:
            raise ValueError(f"Recipe composition cycle: {' -> '.join((*trail, recipe))}")
        source = by_name.get(recipe)
        children = source.recipe_list if source is not None else []
        direct_children = [_configured_recipe(item)[0] for item in children]
        effects = _direct_effects(recipe, {}, semantic_rules, [recipe])
        for child in children:
            child_recipe, options = _configured_recipe(child)
            configured = _direct_effects(
                child_recipe, options, semantic_rules, [recipe, child_recipe]
            )
            effects.extend(configured)
            if child_recipe in by_name:
                child_knowledge = resolve(child_recipe, (*trail, recipe))
                effects.extend(
                    effect.model_copy(
                        update={"path": [recipe, *effect.path]}
                    )
                    for effect in child_knowledge.effects
                )
        knowledge = ResolvedRecipeKnowledge(
            recipe=recipe,
            repository=source.repository if source else None,
            source_revision=source.source_revision if source else None,
            artifact=source.artifact if source else None,
            direct_children=direct_children,
            effects=_deduplicate_effects(effects),
        )
        resolved[recipe] = knowledge
        return knowledge

    for recipe in sorted(by_name):
        resolve(recipe)
    serialized = json.dumps(
        [source.model_dump(mode="json") for source in sources], sort_keys=True
    ).encode()
    serialized_rules = json.dumps(semantic_rules, sort_keys=True).encode()
    return RecipeKnowledgeSnapshot(
        catalog_fingerprint=f"sha256:{hashlib.sha256(serialized).hexdigest()}",
        semantic_rules_fingerprint=(
            f"sha256:{hashlib.sha256(serialized_rules).hexdigest()}"
        ),
        artifacts=sorted(
            {source.artifact for source in sources if source.artifact is not None}
        ),
        compatibility=semantic_rules.get("compatibility", {}),
        recipes=resolved,
    )


def schedule_recipe_plan(
    plan: RecipePlan,
    knowledge: RecipeKnowledgeSnapshot,
    *,
    target_java: int | None,
    target_gaps: set[str] | None = None,
    target_recipes: dict[str, str] | None = None,
) -> RecipePlan:
    """Complete target gaps and order finalizers after transitive invalidators."""

    recommendations = list(plan.recommendations)
    required = target_recipes or {}
    for target in sorted(target_gaps or set()):
        required_recipe = required.get(target)
        if required_recipe is not None and all(
            item.recipe != required_recipe for item in recommendations
        ):
            recommendations.append(
                _target_recommendation(target, required_recipe, knowledge)
            )
    if target_java is None:
        return RecipePlan(recommendations=recommendations)
    selected = {item.recipe for item in recommendations}
    finalizers: set[str] = set()
    invalidators: set[str] = set()
    for item in recommendations:
        recipe_knowledge = knowledge.recipes.get(item.recipe)
        effects = recipe_knowledge.effects if recipe_knowledge is not None else []
        exact_java_targets: set[int] = set()
        for effect in effects:
            if not effect.property.startswith("build.java."):
                continue
            try:
                value = int(effect.value)
            except (TypeError, ValueError):
                continue
            if value == target_java:
                finalizers.add(item.recipe)
            if effect.property == "build.java.target" and effect.operation == "set_exact":
                exact_java_targets.add(value)
        # A Java upgrade aggregate contains its prerequisite Java stages as well as
        # the requested target. Treat the target guarantee as its net result.
        if exact_java_targets and target_java not in exact_java_targets:
            invalidators.add(item.recipe)
    java_finalizer = required.get("java")
    if invalidators and not finalizers and java_finalizer is not None:
        recommendations.append(
            _target_recommendation("java", java_finalizer, knowledge)
        )
        selected.add(java_finalizer)
        finalizers.add(java_finalizer)
    if not finalizers or not invalidators:
        return RecipePlan(recommendations=recommendations)

    normalized: dict[str, RecipeRecommendation] = {}
    for item in recommendations:
        dependencies = [
            dependency
            for dependency in item.depends_on
            if not (item.recipe in invalidators and dependency in finalizers)
        ]
        if item.recipe in finalizers:
            dependencies = list(dict.fromkeys([*invalidators, *dependencies]))
        normalized[item.recipe] = item.model_copy(update={"depends_on": dependencies})

    ordered: list[RecipeRecommendation] = []
    remaining = [item.recipe for item in recommendations]
    completed: set[str] = set()
    while remaining:
        ready = next(
            (
                recipe
                for recipe in remaining
                if set(normalized[recipe].depends_on) & selected <= completed
            ),
            None,
        )
        if ready is None:
            raise ValueError(f"Recipe constraint cycle: {remaining}")
        ordered.append(normalized[ready])
        completed.add(ready)
        remaining.remove(ready)
    return RecipePlan(recommendations=ordered)


def _target_recommendation(
    target: str,
    recipe: str,
    knowledge: RecipeKnowledgeSnapshot,
) -> RecipeRecommendation:
    recipe_knowledge = knowledge.recipes.get(recipe)
    return RecipeRecommendation(
        recipe=recipe,
        recipe_artifact=(recipe_knowledge.artifact if recipe_knowledge else None),
        reason=f"The selected profile requires its {target} target to be established.",
        evidence=[f"profile.unresolved_target={target}"],
        expected_benefit=f"Deterministically reaches the profile's {target} baseline.",
        priority=RecipePriority.HIGH,
        confidence=1,
    )


def _configured_recipe(item: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if isinstance(item, str):
        return item, {}
    recipe, options = next(iter(item.items()))
    return recipe, options if isinstance(options, dict) else {}


def _direct_effects(
    recipe: str,
    options: dict[str, Any],
    rules: dict[str, Any],
    path: list[str],
) -> list[RecipeEffect]:
    specifications: list[tuple[dict[str, Any], re.Match[str] | None]] = []
    for specification in rules.get("leaf_effects", {}).get(recipe, []):
        specifications.append((specification, None))
    for pattern in rules.get("recipe_patterns", []):
        match = re.fullmatch(str(pattern["pattern"]), recipe)
        if match:
            specifications.extend((effect, match) for effect in pattern["effects"])

    effects: list[RecipeEffect] = []
    for specification, match in specifications:
        if "value_from" in specification:
            value = options.get(specification["value_from"])
        elif "value_from_match" in specification and match is not None:
            value = match.group(str(specification["value_from_match"]))
        elif "value_template" in specification and match is not None:
            value = str(specification["value_template"]).format(**match.groupdict())
        else:
            value = specification.get("value")
        if value is None:
            continue
        effects.append(
            RecipeEffect(
                property=str(specification["property"]),
                operation=str(specification["operation"]),
                value=int(value) if str(value).isdigit() else str(value),
                introduced_by=recipe,
                path=path,
            )
        )
    return effects


def _deduplicate_effects(effects: list[RecipeEffect]) -> list[RecipeEffect]:
    unique: dict[tuple[str, str, str, str], RecipeEffect] = {}
    for effect in effects:
        key = (
            effect.property,
            effect.operation,
            str(effect.value),
            effect.introduced_by,
        )
        unique[key] = effect
    return list(unique.values())
