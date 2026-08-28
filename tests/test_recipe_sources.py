from refinery_ai.recipe_sources import RECIPE_REPOSITORIES, parse_recipe_yaml


def test_parse_recipe_yaml_builds_planner_objects_with_source_paths() -> None:
    recipes = parse_recipe_yaml(
        """
---
type: specs.openrewrite.org/v1beta/recipe
name: org.openrewrite.java.migrate.UpgradeToJava21
displayName: Migrate to Java 21
description: >-
  Apply changes commonly needed
  when migrating to Java 21.
recipeList:
  - org.openrewrite.java.migrate.UpgradeToJava17
---
type: specs.openrewrite.org/v1beta/style
name: ignored.style
description: Not a recipe
        """,
        "src/main/resources/META-INF/rewrite/java-version-21.yml",
    )

    assert [recipe.model_dump() for recipe in recipes] == [
        {
            "recipe": "org.openrewrite.java.migrate.UpgradeToJava21",
            "display_name": "Migrate to Java 21",
            "description": "Apply changes commonly needed when migrating to Java 21.",
            "repository": "openrewrite/rewrite-migrate-java",
            "file_path": "src/main/resources/META-INF/rewrite/java-version-21.yml",
        }
    ]


def test_recipe_repositories_include_java_migrations_and_spring() -> None:
    assert RECIPE_REPOSITORIES == (
        "openrewrite/rewrite-migrate-java",
        "openrewrite/rewrite-spring",
    )


def test_parse_recipe_yaml_records_spring_repository_provenance() -> None:
    recipes = parse_recipe_yaml(
        """
type: specs.openrewrite.org/v1beta/recipe
name: org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5
displayName: Migrate to Spring Boot 3.5
description: Migrate applications to Spring Boot 3.5.
        """,
        "src/main/resources/META-INF/rewrite/spring-boot-35.yml",
        repository="openrewrite/rewrite-spring",
    )

    assert recipes[0].repository == "openrewrite/rewrite-spring"
    assert recipes[0].recipe == "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
