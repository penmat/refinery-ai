"""Typed migration implementations and dispatch, imported lazily to avoid cycles."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from refinery_ai.migrations.openrewrite import OpenRewriteMigrationRunner

__all__ = ["OpenRewriteMigrationRunner"]


def __getattr__(name: str) -> Any:
    if name == "OpenRewriteMigrationRunner":
        from refinery_ai.migrations.openrewrite import OpenRewriteMigrationRunner

        return OpenRewriteMigrationRunner
    raise AttributeError(name)
