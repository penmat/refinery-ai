from pathlib import Path

from refinery_ai.migrations.utf8 import (
    UTF8_APPLICATION_PROPERTIES_RECIPE,
    Utf8PropertiesMigrationRunner,
)
from refinery_ai.models import DiscoverySnapshot, RecipeRecommendation


def test_utf8_runner_converts_iso_8859_1_properties(tmp_path: Path) -> None:
    relative = Path("src/main/resources/application.properties")
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes("# Interface Gráfica\n".encode("iso-8859-1"))
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system="maven",
        non_utf8_application_properties=[relative],
    )
    step = RecipeRecommendation(
        execution_type="utf8",
        recipe=UTF8_APPLICATION_PROPERTIES_RECIPE,
        reason="Convert encoding.",
        evidence=[f"discovery.non_utf8_application_properties={relative}"],
        expected_benefit="UTF-8 resources.",
        priority="high",
        confidence=1,
    )

    result = Utf8PropertiesMigrationRunner().run(snapshot, step, apply=True, timeout=10)

    assert result.green
    assert path.read_text(encoding="utf-8") == "# Interface Gráfica\n"


def test_utf8_runner_skips_a_file_that_is_already_utf8(tmp_path: Path) -> None:
    relative = Path("src/main/resources/application.properties")
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("# Interface Gráfica\n", encoding="utf-8")
    original = path.read_bytes()
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system="maven",
        non_utf8_application_properties=[relative],
    )
    step = RecipeRecommendation(
        execution_type="utf8",
        recipe=UTF8_APPLICATION_PROPERTIES_RECIPE,
        reason="Convert encoding.",
        evidence=[f"discovery.non_utf8_application_properties={relative}"],
        expected_benefit="UTF-8 resources.",
        priority="high",
        confidence=1,
    )

    result = Utf8PropertiesMigrationRunner().run(snapshot, step, apply=True, timeout=10)

    assert result.green
    assert path.read_bytes() == original
