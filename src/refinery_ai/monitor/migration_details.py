"""Shared human-readable migration descriptions for recorded monitor events."""

from collections.abc import Iterable

from refinery_ai.monitor.models import PlannedMigration


def describe_migrations(recipes: Iterable[str]) -> list[PlannedMigration]:
    from refinery_ai.migrations.runner import migration_display_name

    return [
        PlannedMigration(recipe=recipe, name=migration_display_name(recipe)) for recipe in recipes
    ]
