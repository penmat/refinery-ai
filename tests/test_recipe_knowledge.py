from pathlib import Path

from refinery_ai.models import RecipePlan, RecipePriority, RecipeRecommendation
from refinery_ai.recipes.knowledge import (
    build_recipe_knowledge,
    schedule_recipe_plan,
)
from refinery_ai.recipes.sources import RecipeSource


def source(recipe: str, children: list[str | dict[str, object]]) -> RecipeSource:
    return RecipeSource(
        recipe=recipe,
        display_name=recipe,
        description="Fixture recipe.",
        repository="openrewrite/rewrite-spring",
        file_path="META-INF/rewrite/fixture.yml",
        recipe_list=children,
        source_revision="v6.37.1",
        artifact="org.openrewrite.recipe:rewrite-spring:6.37.1",
    )


def recommendation(
    recipe: str, depends_on: list[str] | None = None
) -> RecipeRecommendation:
    return RecipeRecommendation(
        recipe=recipe,
        reason="Required platform migration.",
        evidence=["profile target"],
        expected_benefit="Reach the selected platform baseline.",
        priority=RecipePriority.HIGH,
        confidence=1,
        depends_on=depends_on or [],
    )


def knowledge_sources() -> list[RecipeSource]:
    return [
        source(
            "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
            ["org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0"],
        ),
        source(
            "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
            ["org.openrewrite.java.migrate.UpgradeToJava17"],
        ),
        source(
            "org.openrewrite.java.migrate.UpgradeToJava17",
            [
                {
                    "org.openrewrite.java.migrate.UpgradeJavaVersion": {
                        "version": 17
                    }
                }
            ],
        ),
        source(
            "org.openrewrite.java.migrate.UpgradeBuildToJava21",
            [
                {
                    "org.openrewrite.java.migrate.UpgradeJavaVersion": {
                        "version": 21
                    }
                }
            ],
        ),
        source(
            "org.openrewrite.java.migrate.UpgradeToJava21",
            [
                "org.openrewrite.java.migrate.UpgradeToJava17",
                "org.openrewrite.java.migrate.UpgradeBuildToJava21",
            ],
        ),
        source("org.openrewrite.java.migrate.UpgradePluginsForJava21", []),
    ]


def test_knowledge_resolves_transitive_effects_with_provenance(tmp_path: Path) -> None:
    knowledge = build_recipe_knowledge(knowledge_sources())
    boot = knowledge.recipes[
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
    ]
    java_effect = next(
        effect
        for effect in boot.effects
        if effect.property == "build.java.target"
        and effect.introduced_by == "org.openrewrite.java.migrate.UpgradeJavaVersion"
    )

    assert java_effect.value == 17
    assert java_effect.introduced_by == "org.openrewrite.java.migrate.UpgradeJavaVersion"
    assert java_effect.path == [
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "org.openrewrite.java.migrate.UpgradeToJava17",
        "org.openrewrite.java.migrate.UpgradeJavaVersion",
    ]
    assert knowledge.artifacts == ["org.openrewrite.recipe:rewrite-spring:6.37.1"]
    assert knowledge.compatibility["spring_boot"]["3.5"]["java_minimum"] == 17
    assert knowledge.semantic_rules_fingerprint.startswith("sha256:")

    path = tmp_path / "recipe-knowledge.json"
    knowledge.persist(path)
    assert '"catalog_fingerprint": "sha256:' in path.read_text()
    assert '"compatibility"' in path.read_text()


def test_scheduler_derives_java21_after_boot_from_transitive_effects() -> None:
    knowledge = build_recipe_knowledge(knowledge_sources())
    boot = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
    java_build = "org.openrewrite.java.migrate.UpgradeBuildToJava21"
    java_plugins = "org.openrewrite.java.migrate.UpgradePluginsForJava21"
    plan = RecipePlan(
        recommendations=[
            recommendation(java_build, [boot]),
            recommendation(java_plugins),
            recommendation(boot, [java_build]),
        ]
    )

    result = schedule_recipe_plan(plan, knowledge, target_java=21)

    assert [item.recipe for item in result.recommendations] == [
        boot,
        java_build,
        java_plugins,
    ]
    assert result.recommendations[0].depends_on == []
    assert result.recommendations[1].depends_on == [boot]
    assert result.recommendations[2].depends_on == [boot]


def test_scheduler_leaves_unrelated_plan_order_unchanged() -> None:
    knowledge = build_recipe_knowledge(knowledge_sources())
    plan = RecipePlan(
        recommendations=[
            recommendation("example.First"),
            recommendation("example.Second"),
        ]
    )

    assert schedule_recipe_plan(plan, knowledge, target_java=21) == plan


def test_scheduler_treats_java_upgrade_aggregate_as_target_guarantee() -> None:
    knowledge = build_recipe_knowledge(knowledge_sources())
    boot = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
    java = "org.openrewrite.java.migrate.UpgradeToJava21"
    plan = RecipePlan(
        recommendations=[
            recommendation(java),
            recommendation(boot, [java]),
        ]
    )

    result = schedule_recipe_plan(plan, knowledge, target_java=21)

    assert [item.recipe for item in result.recommendations] == [boot, java]
    assert result.recommendations[0].depends_on == []
    assert result.recommendations[1].depends_on == [boot]


def test_scheduler_completes_boot_gap_and_restores_java_after_boot() -> None:
    knowledge = build_recipe_knowledge(knowledge_sources())
    boot = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
    java = "org.openrewrite.java.migrate.UpgradeBuildToJava21"

    result = schedule_recipe_plan(
        RecipePlan(),
        knowledge,
        target_java=21,
        target_gaps={"spring_boot"},
        target_recipes={"java": java, "spring_boot": boot},
    )

    assert [item.recipe for item in result.recommendations] == [boot, java]
    assert result.recommendations[0].recipe_artifact == (
        "org.openrewrite.recipe:rewrite-spring:6.37.1"
    )
    assert result.recommendations[1].depends_on == [boot]


def test_scheduler_can_append_profile_targets_to_ten_planner_suggestions() -> None:
    knowledge = build_recipe_knowledge(knowledge_sources())
    boot = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
    java = "org.openrewrite.java.migrate.UpgradeBuildToJava21"
    plan = RecipePlan(
        recommendations=[recommendation(f"example.Recipe{index}") for index in range(10)]
    )

    result = schedule_recipe_plan(
        plan,
        knowledge,
        target_java=21,
        target_gaps={"spring_boot"},
        target_recipes={"java": java, "spring_boot": boot},
    )

    assert len(result.recommendations) == 12
    assert [item.recipe for item in result.recommendations[-2:]] == [boot, java]
