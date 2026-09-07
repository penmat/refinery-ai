from refinery_ai.recipes.catalog import RecipeCatalogSession
from refinery_ai.recipes.sources import RecipeSource


def source(recipe: str, display_name: str, description: str) -> RecipeSource:
    return RecipeSource(
        recipe=recipe,
        display_name=display_name,
        description=description,
        repository="openrewrite/rewrite-spring",
        file_path="src/main/resources/META-INF/rewrite/recipe.yml",
        recipe_list=["org.openrewrite.java.migrate.UpgradeToJava17"],
    )


def test_search_is_bounded_and_marks_only_returned_recipes_as_allowed() -> None:
    catalog = RecipeCatalogSession(
        [
            source(
                "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
                "Migrate to Spring Boot 3.5",
                "Upgrade Spring Boot applications.",
            ),
            source(
                "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7",
                "Migrate to Spring Boot 2.7",
                "Upgrade Spring Boot applications.",
            ),
        ]
    )

    results = catalog.search("Spring Boot", limit=1)

    assert len(results) == 1
    assert catalog.allowed_recipes == {results[0].recipe}
    assert catalog.recipe_artifacts[results[0].recipe] == (
        "org.openrewrite.recipe:rewrite-spring:RELEASE"
    )


def test_get_returns_composition_and_categories_do_not_allow_recipes() -> None:
    recipe = source(
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
        "Migrate to Spring Boot 3.5",
        "Upgrade Spring Boot applications.",
    )
    catalog = RecipeCatalogSession([recipe])

    categories = catalog.list_categories()
    assert categories[0].category == "spring.boot3"
    assert catalog.allowed_recipes == set()

    detail = catalog.get(recipe.recipe)
    assert detail is not None
    assert detail.recipe_list == ["org.openrewrite.java.migrate.UpgradeToJava17"]
    assert catalog.allowed_recipes == {recipe.recipe}


def test_compact_index_exposes_every_recipe_and_makes_it_selectable() -> None:
    recipes = [
        source(
            f"org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_{minor}",
            f"Migrate to Spring Boot 3.{minor}",
            "Upgrade Spring Boot applications.",
        )
        for minor in range(4)
    ]
    catalog = RecipeCatalogSession(recipes)

    candidates = catalog.compact_index()

    assert [candidate.recipe for candidate in candidates] == [
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_1",
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_2",
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_3",
    ]
    assert {candidate.category for candidate in candidates} == {"spring.boot3"}
    assert catalog.allowed_recipes == {candidate.recipe for candidate in candidates}


def test_compact_index_bounds_and_flattens_descriptions() -> None:
    recipe = source("example.Recipe", "Example", "first line\n" + "x" * 300)

    entry = RecipeCatalogSession([recipe]).compact_index()[0]

    assert "\n" not in entry.description
    assert len(entry.description) == 240
