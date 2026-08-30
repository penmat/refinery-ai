from pathlib import Path

from refinery_ai.migrations.spring_properties import (
    DEFER_DATASOURCE_INITIALIZATION_RECIPE,
    SpringPropertiesMigrationRunner,
)
from refinery_ai.models import DiscoverySnapshot, RecipeRecommendation


def test_adds_deferred_datasource_initialization_idempotently(tmp_path: Path) -> None:
    relative = Path("src/main/resources/application.properties")
    properties = tmp_path / relative
    properties.parent.mkdir(parents=True)
    properties.write_text("spring.jpa.show-sql=true\n")
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system="maven",
        deferred_datasource_initialization_properties=[relative],
    )
    step = RecipeRecommendation(
        execution_type="spring-properties",
        recipe=DEFER_DATASOURCE_INITIALIZATION_RECIPE,
        reason="Preserve initialization order.",
        evidence=["data.sql"],
        expected_benefit="Tables exist before inserts.",
        priority="high",
        confidence=1,
    )
    runner = SpringPropertiesMigrationRunner()

    assert runner.run(snapshot, step, apply=True, timeout=10).green
    assert runner.run(snapshot, step, apply=True, timeout=10).green

    assert properties.read_text().count(
        "spring.jpa.defer-datasource-initialization=true"
    ) == 1
