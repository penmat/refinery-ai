import io
import ssl
from pathlib import Path
from urllib.error import HTTPError

import refinery_ai.recipes.sources as recipe_sources_module
from refinery_ai.recipes.sources import (
    RECIPE_REPOSITORIES,
    RecipeSource,
    _ssl_context,
    fetch_recipe_sources,
    parse_recipe_yaml,
)


def test_recipe_source_ssl_context_can_disable_verification() -> None:
    verified = _ssl_context(True)
    insecure = _ssl_context(False)

    assert verified.verify_mode == ssl.CERT_REQUIRED
    assert insecure.verify_mode == ssl.CERT_NONE
    assert insecure.check_hostname is False


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
            "recipe_list": ["org.openrewrite.java.migrate.UpgradeToJava17"],
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


def _recipe(repository: str) -> RecipeSource:
    return RecipeSource(
        recipe=f"{repository}.Recipe",
        display_name="Recipe",
        description="Description",
        repository=repository,
        file_path="src/main/resources/META-INF/rewrite/recipe.yml",
    )


def test_fetch_recipe_sources_caches_each_successful_repository(
    tmp_path: Path, monkeypatch: object
) -> None:
    calls: list[str] = []

    def fetch(repository: str, timeout: float, verify_ssl: bool) -> list[RecipeSource]:
        calls.append(repository)
        return [_recipe(repository)]

    monkeypatch.setattr(recipe_sources_module, "_fetch_repository_recipes", fetch)  # type: ignore[attr-defined]

    recipes = fetch_recipe_sources(cache_directory=tmp_path)

    assert [recipe.repository for recipe in recipes] == list(RECIPE_REPOSITORIES)
    assert calls == list(RECIPE_REPOSITORIES)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "openrewrite--rewrite-migrate-java.json",
        "openrewrite--rewrite-spring.json",
    ]


def test_fetch_recipe_sources_retries_gateway_timeout_then_succeeds(
    monkeypatch: object,
) -> None:
    attempts: dict[str, int] = {}
    delays: list[float] = []

    def fetch(repository: str, timeout: float, verify_ssl: bool) -> list[RecipeSource]:
        attempts[repository] = attempts.get(repository, 0) + 1
        if repository == RECIPE_REPOSITORIES[0] and attempts[repository] < 3:
            raise HTTPError("https://github.com", 504, "Gateway Time-out", {}, io.BytesIO())
        return [_recipe(repository)]

    monkeypatch.setattr(recipe_sources_module, "_fetch_repository_recipes", fetch)  # type: ignore[attr-defined]

    recipes = fetch_recipe_sources(sleep=delays.append)

    assert len(recipes) == 2
    assert attempts[RECIPE_REPOSITORIES[0]] == 3
    assert delays == [0.5, 1.0]


def test_fetch_recipe_sources_uses_cache_without_contacting_upstream(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        recipe_sources_module,
        "_fetch_repository_recipes",
        lambda repository, timeout, verify_ssl: [_recipe(repository)],
    )
    expected = fetch_recipe_sources(cache_directory=tmp_path)
    def fail(repository: str, timeout: float, verify_ssl: bool) -> list[RecipeSource]:
        raise AssertionError("upstream should not be contacted when the cache is populated")

    monkeypatch.setattr(recipe_sources_module, "_fetch_repository_recipes", fail)  # type: ignore[attr-defined]

    actual = fetch_recipe_sources(cache_directory=tmp_path, sleep=lambda _: None)

    assert actual == expected


def test_fetch_recipe_sources_refreshes_on_request_and_falls_back_to_cache(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        recipe_sources_module,
        "_fetch_repository_recipes",
        lambda repository, timeout, verify_ssl: [_recipe(repository)],
    )
    expected = fetch_recipe_sources(cache_directory=tmp_path)
    attempts = 0

    def fail(repository: str, timeout: float, verify_ssl: bool) -> list[RecipeSource]:
        nonlocal attempts
        attempts += 1
        raise HTTPError("https://github.com", 504, "Gateway Time-out", {}, io.BytesIO())

    monkeypatch.setattr(recipe_sources_module, "_fetch_repository_recipes", fail)  # type: ignore[attr-defined]

    actual = fetch_recipe_sources(
        cache_directory=tmp_path,
        refresh=True,
        sleep=lambda _: None,
    )

    assert actual == expected
    assert attempts == 6


def test_fetch_recipe_sources_reloads_legacy_cache_schema(
    tmp_path: Path, monkeypatch: object
) -> None:
    for repository in RECIPE_REPOSITORIES:
        path = tmp_path / f"{repository.replace('/', '--')}.json"
        path.write_text("[]")
    calls: list[str] = []

    def fetch(repository: str, timeout: float, verify_ssl: bool) -> list[RecipeSource]:
        calls.append(repository)
        return [_recipe(repository)]

    monkeypatch.setattr(recipe_sources_module, "_fetch_repository_recipes", fetch)  # type: ignore[attr-defined]

    fetch_recipe_sources(cache_directory=tmp_path)

    assert calls == list(RECIPE_REPOSITORIES)
