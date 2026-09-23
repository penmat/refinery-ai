from pathlib import Path

import pytest

from refinery_ai.migrations.runner import MigrationRunner
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    PersistedMigrationPlan,
    RecipePlan,
    RecipeRecommendation,
)
from refinery_ai.planning_artifacts import MigrationPlanStore
from refinery_ai.profiles import EvidenceMode, available_profiles, load_profile


def recommendation(recipe: str, depends_on: list[str] | None = None) -> RecipeRecommendation:
    return RecipeRecommendation(
        recipe=recipe,
        reason="Modernize.",
        evidence=["fixture"],
        expected_benefit="Modern baseline.",
        priority="high",
        confidence=1,
        depends_on=depends_on or [],
    )


def test_java17_profile_limits_versions_and_prunes_dependencies() -> None:
    profile = load_profile("spring-boot-3.2-java-17")
    boot32 = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_2"
    boot33 = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_3"
    java21 = "org.openrewrite.java.migrate.UpgradeToJava21"

    result = profile.filter_plan(
        RecipePlan(
            recommendations=[
                recommendation(boot32),
                recommendation(boot33, [boot32]),
                recommendation(java21, [boot33]),
            ]
        )
    )

    assert available_profiles() == [
        "spring-boot-3.2-java-17",
        "spring-boot-3.5-java-21",
    ]
    assert [item.recipe for item in result.recommendations] == [boot32]
    assert result.recommendations[0].depends_on == []
    assert profile.target_java == 17
    assert profile.target_spring_boot == "3.2"
    assert profile.target_recipes == {
        "java": "org.openrewrite.java.migrate.UpgradeToJava17",
        "spring_boot": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_2",
    }
    assert profile.evidence.context_graph is EvidenceMode.OFF
    assert profile.evidence.sonarqube is EvidenceMode.OFF


def test_java21_profile_limits_platform_versions_and_optional_language_rewrites() -> None:
    profile = load_profile("spring-boot-3.5-java-21")
    java21 = "org.openrewrite.java.migrate.UpgradeToJava21"
    boot35 = "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
    java25 = "org.openrewrite.java.migrate.UpgradeToJava25"
    boot40 = "org.openrewrite.java.spring.boot4.UpgradeSpringBoot_4_0"
    records = "org.openrewrite.staticanalysis.UseRecords"
    pattern_matching = "org.openrewrite.java.migrate.lang.UsePatternMatchingForInstanceOf"
    switch_expression = "org.openrewrite.staticanalysis.UseSwitchExpression"
    text_blocks = "org.openrewrite.java.migrate.lang.UseTextBlocks"
    var = "org.openrewrite.staticanalysis.UseVar"
    virtual_threads = "org.openrewrite.java.migrate.lang.UseVirtualThreads"

    result = profile.filter_plan(
        RecipePlan(
            recommendations=[
                recommendation(java21),
                recommendation(boot35, [java21]),
                recommendation(records, [java21]),
                recommendation(pattern_matching, [java21]),
                recommendation(switch_expression, [java21]),
                recommendation(text_blocks, [java21]),
                recommendation(var, [java21]),
                recommendation(virtual_threads, [java21]),
                recommendation(java25, [java21]),
                recommendation(boot40, [boot35]),
            ]
        )
    )

    assert [item.recipe for item in result.recommendations] == [java21, boot35]
    assert result.recommendations[1].depends_on == [java21]
    assert profile.target_java == 21
    assert profile.target_spring_boot == "3.5"
    assert profile.target_recipes["java"] == (
        "org.openrewrite.java.migrate.UpgradeBuildToJava21"
    )
    assert profile.evidence.context_graph is EvidenceMode.OFF
    assert profile.evidence.sonarqube is EvidenceMode.OFF


def test_java21_profile_reports_unresolved_platform_targets(tmp_path: Path) -> None:
    profile = load_profile("spring-boot-3.5-java-21")
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system=BuildSystem.GRADLE,
        java_version="17.0.12",
        spring_boot_version="3.2.9",
    )

    gaps = profile.unresolved_targets(snapshot)

    assert [(gap.target, gap.target_version) for gap in gaps] == [
        ("java", "21"),
        ("spring_boot", "3.5"),
    ]


def test_profile_reports_only_unresolved_structured_targets(tmp_path: Path) -> None:
    profile = load_profile("spring-boot-3.2-java-17")
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system=BuildSystem.MAVEN,
        java_version="17",
        spring_boot_version="3.0.13",
    )

    gaps = profile.unresolved_targets(snapshot)

    assert [gap.target for gap in gaps] == ["spring_boot"]
    assert gaps[0].current_version == "3.0.13"
    assert gaps[0].target_version == "3.2"


def test_migration_rejects_plan_from_another_profile(tmp_path: Path) -> None:
    store = MigrationPlanStore(tmp_path)
    store.root.mkdir(parents=True)
    plan = PersistedMigrationPlan(
        plan_id="plan1",
        profile="spring-boot-3.2-java-17",
        snapshot=DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN),
    )
    (store.root / "plan1.json").write_text(plan.model_dump_json())

    with pytest.raises(ValueError, match="requested profile"):
        MigrationRunner(store).run(expected_profile="another-profile")


def test_unknown_profile_is_actionable() -> None:
    with pytest.raises(FileNotFoundError, match="Available profiles"):
        load_profile("missing")
