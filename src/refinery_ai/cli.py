"""Command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from refinery_ai.factory import ModernizationFactory
from refinery_ai.harness import HarnessRepositoryEnvironment
from refinery_ai.models import ModernizationGoal, RunReport

app = typer.Typer(help="Safe, auditable Java modernization.", no_args_is_help=True)


def _print(report: RunReport) -> None:
    typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))


@app.command()
def version() -> None:
    """Show the installed version."""

    from refinery_ai import __version__

    typer.echo(__version__)


@app.command("discover")
def discover_command(
    repository: Annotated[Path, typer.Argument(exists=True, file_okay=False)] = Path("."),
) -> None:
    """Discover a Java repository without changing it."""

    environment = HarnessRepositoryEnvironment(repository)
    typer.echo(json.dumps(environment.discover(repository).model_dump(mode="json"), indent=2))


@app.command()
def graph(
    question: Annotated[str, typer.Argument(help="Question for the code knowledge graph")],
    repository: Annotated[Path, typer.Option("--repository", "-r")] = Path("."),
) -> None:
    """Build the Graphify graph and return a scoped query result."""

    environment = HarnessRepositoryEnvironment(repository)
    info = environment.build_knowledge(repository)
    typer.echo(environment.query_knowledge(info.graph_path, question))


@app.command()
def plan(
    goal: Annotated[str, typer.Argument(help="Modernization goal")],
    repository: Annotated[Path, typer.Option("--repository", "-r")] = Path("."),
    target_java: Annotated[int | None, typer.Option("--target-java")] = None,
) -> None:
    """Build and persist a deterministic modernization plan."""

    _print(
        ModernizationFactory().prepare(
            repository, ModernizationGoal(description=goal, target_java=target_java)
        )
    )


@app.command()
def run(
    goal: Annotated[str, typer.Argument(help="Modernization goal")],
    repository: Annotated[Path, typer.Option("--repository", "-r")] = Path("."),
    target_java: Annotated[int | None, typer.Option("--target-java")] = None,
    apply: Annotated[bool, typer.Option(help="Run approved commands and dry runs")] = False,
) -> None:
    """Run the factory; defaults to a non-mutating plan."""

    _print(
        ModernizationFactory().run(
            repository,
            ModernizationGoal(description=goal, target_java=target_java),
            apply=apply,
        )
    )


if __name__ == "__main__":
    app()
