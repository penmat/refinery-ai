from pathlib import Path

from refinery_ai.migrations.maven_property import (
    SET_SPRING_BOOT_VERSION_PREFIX,
    MavenPropertyMigrationRunner,
)
from refinery_ai.models import (
    BuildSystem,
    DiscoverySnapshot,
    ExecutionType,
    RecipePriority,
    RecipeRecommendation,
)


def test_updates_custom_spring_boot_property_idempotently(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(
        "<project><properties><spring-boot.version>2.7.18</spring-boot.version>"
        "</properties></project>\n"
    )
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)
    step = RecipeRecommendation(
        execution_type=ExecutionType.MAVEN_PROPERTY,
        recipe=f"{SET_SPRING_BOOT_VERSION_PREFIX}3.2.12",
        reason="Reach the profile target.",
        evidence=["custom property"],
        expected_benefit="Use Boot 3.2.12.",
        priority=RecipePriority.HIGH,
        confidence=1,
    )
    runner = MavenPropertyMigrationRunner()

    assert runner.run(snapshot, step, apply=True, timeout=10).green
    assert runner.run(snapshot, step, apply=True, timeout=10).green
    assert pom.read_text().count("<spring-boot.version>3.2.12</spring-boot.version>") == 1
