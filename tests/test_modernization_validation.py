import pytest

from refinery_ai.modernization.models import TransformationSpec
from refinery_ai.modernization.validation import validate_transformation_scope
from refinery_ai.recipes.catalog import RecipeCatalogSession


def test_index_does_not_prove_inspection():
    catalog = RecipeCatalogSession([])
    catalog.compact_index()
    assert catalog.inspected_recipes == set()


def test_minimal_rejects_optional_composite_effect():
    step = TransformationSpec(
        recipe="org.openrewrite.java.migrate.UpgradeToJava21",
        artifact="g:a:1",
        effects=["compatibility"],
    )
    with pytest.raises(ValueError):
        validate_transformation_scope(step, "minimal", RecipeCatalogSession([]))


def test_unknown_composition_is_not_safe():
    with pytest.raises(ValueError):
        validate_transformation_scope(
            TransformationSpec(recipe="unknown", artifact="g:a:1"),
            "recommended",
            RecipeCatalogSession([]),
        )


def test_artifact_must_match_inspected_recipe():
    from refinery_ai.recipes.sources import RecipeSource

    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="example.Source",
                display_name="Source",
                description="Source",
                repository="example/recipes",
                file_path="recipes.yml",
                artifact="g:a:1",
            )
        ]
    )
    catalog.get("example.Source")
    with pytest.raises(ValueError, match="artifact"):
        validate_transformation_scope(
            TransformationSpec(recipe="example.Source", artifact="g:other:2"),
            "recommended",
            catalog,
        )


@pytest.mark.parametrize("version", ["3.x", "3.X", "3.2.x", "3.2.+", "3.0-SNAPSHOT"])
def test_release_targets_reject_selectors(version):
    from refinery_ai.modernization.models import exact_version

    with pytest.raises(ValueError, match="concrete"):
        exact_version(version)


def test_dependency_recipe_requires_options():
    from refinery_ai.modernization.transformations import validate_options

    with pytest.raises(ValueError, match="option"):
        validate_options("org.openrewrite.maven.UpgradeDependencyVersion", {})


def test_composite_cannot_hide_dynamic_package_target():
    from refinery_ai.recipes.sources import RecipeSource

    catalog = RecipeCatalogSession(
        [
            RecipeSource(
                recipe="example.Composite",
                display_name="Composite",
                description="Upgrade",
                repository="example/recipes",
                file_path="recipe.yml",
                artifact="g:a:1",
                recipe_list=[
                    {
                        "org.openrewrite.maven.UpgradeDependencyVersion": {
                            "groupId": "g",
                            "artifactId": "a",
                            "newVersion": "3.x",
                        }
                    }
                ],
            )
        ]
    )
    catalog.get("example.Composite")
    with pytest.raises(ValueError, match=r"concrete|composition"):
        validate_transformation_scope(
            TransformationSpec(recipe="example.Composite", artifact="g:a:1"), "recommended", catalog
        )
