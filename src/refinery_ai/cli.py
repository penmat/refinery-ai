"""Command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from refinery_ai.assessment.comparison import compare
from refinery_ai.assessment.engine import AssessmentEngine
from refinery_ai.assessment.reporting import assessment_text, comparison_text
from refinery_ai.assessment.storage import AssessmentStore
from refinery_ai.config import Settings
from refinery_ai.factory import ModernizationFactory
from refinery_ai.harness import HarnessRepositoryEnvironment
from refinery_ai.models import ModernizationGoal, RunReport
from refinery_ai.summary import ProjectSummaryService, SummaryError
from refinery_ai.summary.reporting import summary_markdown

app = typer.Typer(help="Safe, auditable Java modernization.", no_args_is_help=True)
assessment_app = typer.Typer(help="Inspect and compare saved assessments.", no_args_is_help=True)
app.add_typer(assessment_app, name="assessment")


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


@app.command("assess")
def assess_command(
    repository: Annotated[Path, typer.Argument(exists=True, file_okay=False)] = Path("."),
    name: Annotated[
        str | None, typer.Option("--name", help="Friendly name for this assessment")
    ] = None,
    compare_to: Annotated[
        str | None, typer.Option("--compare", help="Saved assessment ID or name")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON")
    ] = False,
    sonarqube: Annotated[
        bool | None,
        typer.Option("--sonarqube/--no-sonarqube", help="Run temporary local SonarQube analysis"),
    ] = None,
    keep_sonarqube: Annotated[
        bool, typer.Option("--keep-sonarqube", help="Keep the temporary server for diagnosis")
    ] = False,
) -> None:
    """Assess a Java repository using deterministic local evidence."""

    store = AssessmentStore(repository)
    previous = store.load(compare_to) if compare_to else None
    settings = Settings()
    enabled = settings.sonarqube_enabled if sonarqube is None else sonarqube
    if enabled:
        typer.echo(
            f"Running temporary local SonarQube analysis with {settings.sonarqube_image}...",
            err=True,
        )
    current = AssessmentEngine(
        sonarqube_enabled=enabled,
        keep_sonarqube=keep_sonarqube,
        settings=settings,
    ).assess(repository, name=name)
    store.save(current)
    if previous:
        delta = compare(previous, current)
        typer.echo(
            json.dumps(delta.model_dump(mode="json"), indent=2)
            if json_output
            else comparison_text(previous, current, delta)
        )
    else:
        typer.echo(
            json.dumps(current.model_dump(mode="json"), indent=2)
            if json_output
            else assessment_text(current)
        )


@assessment_app.command("show")
def assessment_show(
    reference: Annotated[str, typer.Argument(help="Assessment ID or friendly name")],
    repository: Annotated[Path, typer.Option("--repository", "-r")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a saved assessment."""

    assessment = AssessmentStore(repository).load(reference)
    typer.echo(
        json.dumps(assessment.model_dump(mode="json"), indent=2)
        if json_output
        else assessment_text(assessment)
    )


@assessment_app.command("list")
def assessment_list(
    repository: Annotated[Path, typer.Option("--repository", "-r")] = Path("."),
) -> None:
    """List saved assessments."""

    for assessment in AssessmentStore(repository).list():
        label = f" ({assessment.name})" if assessment.name else ""
        typer.echo(f"{assessment.assessment_id}{label}  {_score_text(assessment.score.overall)}")


@assessment_app.command("compare")
def assessment_compare(
    previous_reference: Annotated[str, typer.Argument(help="Earlier assessment ID or name")],
    current_reference: Annotated[str, typer.Argument(help="Later assessment ID or name")],
    repository: Annotated[Path, typer.Option("--repository", "-r")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare two saved assessments."""

    store = AssessmentStore(repository)
    previous = store.load(previous_reference)
    current = store.load(current_reference)
    delta = compare(previous, current)
    typer.echo(
        json.dumps(delta.model_dump(mode="json"), indent=2)
        if json_output
        else comparison_text(previous, current, delta)
    )


def _score_text(value: float | None) -> str:
    return "not assessed" if value is None else f"score={value:g}"


@app.command()
def graph(
    question: Annotated[str, typer.Argument(help="Question for the code knowledge graph")],
    repository: Annotated[Path, typer.Option("--repository", "-r")] = Path("."),
) -> None:
    """Build the repository context graph and return a scoped query result."""

    environment = HarnessRepositoryEnvironment(repository)
    environment.discover(repository)
    info = environment.build_context(repository)
    typer.echo(environment.query_context(info.graph_path, question))


@app.command()
def summary(
    repository: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="Java repository to summarize")
    ] = Path("."),
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Rebuild the context graph before summarizing")
    ] = False,
    graph_path: Annotated[
        Path | None, typer.Option("--graph", help="Read a specific context graph")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON")
    ] = False,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write the summary to a file")
    ] = None,
    top: Annotated[int, typer.Option("--top", min=1, help="Prominent components to show")] = 10,
    include_tests: Annotated[
        bool, typer.Option("--include-tests", help="Include test graph components")
    ] = False,
    sonarqube: Annotated[
        bool | None,
        typer.Option("--sonarqube/--no-sonarqube", help="Run temporary local SonarQube analysis"),
    ] = None,
    keep_sonarqube: Annotated[
        bool, typer.Option("--keep-sonarqube", help="Keep the temporary server for diagnosis")
    ] = False,
) -> None:
    """Summarize project architecture and modernization posture."""

    settings = Settings()
    enabled = settings.sonarqube_enabled if sonarqube is None else sonarqube
    if enabled:
        typer.echo(
            f"Running temporary local SonarQube analysis with {settings.sonarqube_image}...",
            err=True,
        )
    try:
        result = ProjectSummaryService().summarize(
            repository,
            graph_path=graph_path,
            refresh=refresh,
            include_tests=include_tests,
            top=top,
            sonarqube_enabled=enabled,
            keep_sonarqube=keep_sonarqube,
            settings=settings,
        )
    except SummaryError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error
    rendered = (
        json.dumps(result.model_dump(mode="json"), indent=2)
        if json_output or (output is not None and output.suffix.casefold() == ".json")
        else summary_markdown(result, terminal=False)
    )
    output_path = (
        output.expanduser().resolve()
        if output is not None
        else repository.expanduser().resolve()
        / ".refinery"
        / ("summary.json" if json_output else "summary.md")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered)
    if output is not None:
        typer.echo(f"Summary written to {output_path}")
    else:
        typer.echo(rendered, nl=False)


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
