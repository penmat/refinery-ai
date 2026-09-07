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


def test_builtin_profile_limits_versions_and_prunes_dependencies() -> None:
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

    assert available_profiles() == ["spring-boot-3.2-java-17"]
    assert [item.recipe for item in result.recommendations] == [boot32]
    assert result.recommendations[0].depends_on == []
    assert profile.target_java == 17
    assert profile.target_spring_boot == "3.2"
    assert profile.evidence.context_graph is EvidenceMode.OFF
    assert profile.evidence.sonarqube is EvidenceMode.OFF


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
