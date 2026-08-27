import ssl
from pathlib import Path

from refinery_ai.models import ExecutionType, RecipeRecommendation
from refinery_ai.planning_artifacts import load_planning_artifacts
from refinery_ai.recipe_catalog import _ssl_context, parse_java_migration_catalog


def test_parses_categories_and_recipes_from_catalog_article() -> None:
    catalog = parse_java_migration_catalog(
        """
        <main>
          <h2>Categories</h2>
          <ul><li><a href="/recipes/java/migrate/jakarta">Jakarta</a></li></ul>
          <h2>Composite Recipes</h2>
          <a href="/recipes/java/migrate/upgradetojava21">Migrate to Java 21</a>
          <h2>Recipes</h2>
          <a href="/recipes/java/migrate/upgradebuildtojava21">Upgrade build to Java 21</a>
        </main>
        """
    )

    assert [entry.name for entry in catalog.categories] == ["Jakarta"]
    assert [entry.name for entry in catalog.recipes] == [
        "Migrate to Java 21",
        "Upgrade build to Java 21",
    ]
    context = catalog.planning_context()
    assert "Categories:\n- Jakarta" in context
    assert "Recipes:\n- [Composite Recipes] Migrate to Java 21" in context
    assert not context.lstrip().startswith("{")


def test_planning_artifacts_include_summary_assessment_and_osv(tmp_path: Path) -> None:
    refinery = tmp_path / ".refinery"
    (refinery / "dependencies").mkdir(parents=True)
    (refinery / "summary.md").write_text("Java 11 project")
    (refinery / "assessment.json").write_text('{"runtime":{"java":11}}')
    (refinery / "dependencies/osv-findings-a.json").write_text('{"findings":[]}')

    context = load_planning_artifacts(tmp_path, Path(".refinery"), 10_000)

    assert "summary.md" in context
    assert "assessment.json" in context
    assert "osv-findings-a.json" in context


def test_recipe_recommendations_identify_the_execution_type() -> None:
    recommendation = RecipeRecommendation(
        recipe="org.openrewrite.java.migrate.UpgradeToJava21",
        reason="The project targets Java 11.",
        evidence=["pom.xml maven.compiler.release=11"],
        expected_benefit="Moves the project to the requested Java baseline.",
        priority="high",
        confidence=0.98,
    )

    assert recommendation.execution_type is ExecutionType.OPENREWRITE
    assert recommendation.model_dump(mode="json")["execution_type"] == "openrewrite"


def test_catalog_ssl_verification_can_be_explicitly_disabled() -> None:
    verified = _ssl_context(True)
    insecure = _ssl_context(False)

    assert verified.verify_mode is ssl.CERT_REQUIRED
    assert verified.check_hostname is True
    assert insecure.verify_mode is ssl.CERT_NONE
    assert insecure.check_hostname is False
