"""Bounded, auditable recipe catalog retrieval for the planning agent."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from refinery_ai.recipes.sources import RecipeSource

MAX_RECIPE_SEARCH_RESULTS = 20
MAX_INDEX_DESCRIPTION_CHARACTERS = 240


class RecipeCategory(BaseModel):
    repository: str
    category: str
    recipe_count: int


class RecipeSummary(BaseModel):
    recipe: str
    display_name: str
    description: str
    repository: str


class RecipeIndexEntry(RecipeSummary):
    category: str


class RecipeDetail(RecipeSummary):
    file_path: str
    recipe_list: list[str | dict[str, Any]] = Field(default_factory=list)


def _category(source: RecipeSource) -> str:
    namespace = source.recipe.removeprefix("org.openrewrite.java.")
    parts = namespace.split(".")
    if len(parts) >= 2 and parts[0] in {"migrate", "spring"}:
        return ".".join(parts[:2])
    return parts[0]


def _terms(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


class RecipeCatalogSession:
    """Search one planning run's local catalog and track exactly what it retrieved."""

    def __init__(self, sources: list[RecipeSource]) -> None:
        self._sources = tuple(sources)
        self._by_name = {source.recipe: source for source in sources}
        self.allowed_recipes: set[str] = set()
        self.recipe_artifacts: dict[str, str] = {}

    @property
    def recipe_count(self) -> int:
        return len(self._sources)

    def list_categories(self, repository: str | None = None) -> list[RecipeCategory]:
        """List compact namespaces and counts without retrieving individual recipes."""

        counts = Counter(
            (source.repository, _category(source))
            for source in self._sources
            if repository is None or source.repository == repository
        )
        return [
            RecipeCategory(repository=repo, category=category, recipe_count=count)
            for (repo, category), count in sorted(counts.items())
        ]

    def compact_index(self) -> list[RecipeIndexEntry]:
        """Return a compact view of every recipe and make each entry selectable."""

        self._allow(list(self._sources))
        return [
            RecipeIndexEntry(
                recipe=source.recipe,
                display_name=source.display_name,
                description=" ".join(source.description.split())[
                    :MAX_INDEX_DESCRIPTION_CHARACTERS
                ],
                repository=source.repository,
                category=_category(source),
            )
            for source in self._sources
        ]

    def search(
        self, query: str, repository: str | None = None, limit: int = 10
    ) -> list[RecipeSummary]:
        """Search recipe names and descriptions and mark returned recipes as inspected."""

        bounded_limit = min(max(limit, 1), MAX_RECIPE_SEARCH_RESULTS)
        query_terms = _terms(query)
        if not query_terms:
            return []
        ranked: list[tuple[int, str, RecipeSource]] = []
        for source in self._sources:
            if repository is not None and source.repository != repository:
                continue
            name = f"{source.recipe} {source.display_name}".casefold()
            description = source.description.casefold()
            score = sum(
                (8 if term in name else 0) + (2 if term in description else 0)
                for term in query_terms
            )
            if score:
                ranked.append((score, source.recipe, source))
        matches = [item[2] for item in sorted(ranked, key=lambda item: (-item[0], item[1]))]
        selected = matches[:bounded_limit]
        self._allow(selected)
        return [self._summary(source) for source in selected]

    def get(self, recipe: str) -> RecipeDetail | None:
        """Return the full declarative definition for one exact recipe name."""

        source = self._by_name.get(recipe)
        if source is None:
            return None
        self._allow([source])
        return RecipeDetail(
            **self._summary(source).model_dump(),
            file_path=source.file_path,
            recipe_list=source.recipe_list,
        )

    def _allow(self, sources: list[RecipeSource]) -> None:
        for source in sources:
            self.allowed_recipes.add(source.recipe)
            artifact = source.repository.rsplit("/", maxsplit=1)[-1]
            self.recipe_artifacts[source.recipe] = (
                f"org.openrewrite.recipe:{artifact}:RELEASE"
            )

    @staticmethod
    def _summary(source: RecipeSource) -> RecipeSummary:
        return RecipeSummary(
            recipe=source.recipe,
            display_name=source.display_name,
            description=source.description,
            repository=source.repository,
        )
