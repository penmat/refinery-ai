"""Load evidence and persist planner-to-migration-agent handoffs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from refinery_ai.models import PersistedMigrationPlan, RecipePlan, RunReport


def load_planner_rules() -> str:
    """Load Refinery's maintained OKF planning concepts."""

    root = Path(__file__).with_name("refinery-context")
    documents = sorted(
        path
        for path in root.rglob("*.md")
        if path.name not in {"index.md", "log.md"}
    )
    if not documents:
        raise RuntimeError(f"No planner rule concepts found in OKF bundle {root}")
    try:
        return "\n\n---\n\n".join(path.read_text() for path in documents)
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"Unable to load planner rules from OKF bundle {root}") from error


@dataclass(frozen=True)
class MigrationFailureHistory:
    context: str
    failed_recipes: frozenset[str]
    completed_recipes: frozenset[str]


def load_migration_failures(
    repository: Path,
    state_directory: Path,
    limit: int,
) -> MigrationFailureHistory:
    """Load bounded structured failures for the next planning run."""

    root = repository.expanduser().resolve() / state_directory / "migration-runs"
    candidates = sorted(
        root.glob("*/step-*/step.json") if root.is_dir() else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    failures: list[dict[str, object]] = []
    failed_recipes: set[str] = set()
    completed_recipes: set[str] = set()
    remaining = limit
    for path in candidates:
        try:
            payload = json.loads(path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        recipe_payload = payload.get("recipe")
        if not isinstance(recipe_payload, dict):
            continue
        recipe = recipe_payload.get("recipe")
        if not isinstance(recipe, str):
            continue
        if payload.get("status") == "completed":
            # A green no-op is still authoritative evidence for the revision on
            # which it ran. Remember it so the next planning cycle cannot select
            # the same already-satisfied recipe and stall on a repeated plan.
            completed_recipes.add(recipe)
            continue
        if payload.get("status") != "failed":
            continue
        verification_payload = payload.get("verification")
        verification = verification_payload if isinstance(verification_payload, dict) else {}
        # Maven and Gradle frequently split the useful exception across stdout and
        # stderr. Preserve both so a non-empty but unhelpful stderr stream cannot
        # hide a known, newly repairable failure from the planner.
        stdout = str(verification.get("stdout") or "")
        stderr = str(verification.get("stderr") or "")
        diagnostic = "\n".join(part for part in (stdout, stderr) if part)
        failure = {
            "recipe": recipe,
            "reason": payload.get("reason") or "Migration failed",
            "base_revision": payload.get("base_revision"),
            "build_exit_code": verification.get("exit_code"),
            "build_diagnostic_tail": diagnostic[-2000:],
            "artifacts": str(path.parent.relative_to(repository)),
        }
        rendered = json.dumps(failure, indent=2)
        if len(rendered) > remaining:
            break
        failures.append(failure)
        failed_recipes.add(recipe)
        remaining -= len(rendered)
    context = (
        json.dumps(failures, indent=2)
        if failures
        else "No previous migration failures are recorded."
    )
    return MigrationFailureHistory(
        context=context,
        failed_recipes=frozenset(failed_recipes),
        completed_recipes=frozenset(completed_recipes),
    )


class MigrationPlanStore:
    """Store immutable migration plans beneath the repository state directory."""

    def __init__(self, repository: Path, state_directory: Path = Path(".refinery")) -> None:
        self.repository = repository.expanduser().resolve()
        self.state_directory = state_directory
        self.root = self.repository / state_directory / "plans"

    def save(self, report: RunReport) -> Path | None:
        if not isinstance(report.plan, RecipePlan):
            return None
        artifact = PersistedMigrationPlan(
            plan_id=report.run_id,
            profile=report.profile,
            snapshot=report.snapshot,
            steps=report.plan.recommendations,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{report.run_id}.json"
        path.write_text(artifact.model_dump_json(indent=2) + "\n")
        return path

    def load(self, plan_id: str | None = None) -> PersistedMigrationPlan:
        if plan_id is not None and (not plan_id.isalnum() or len(plan_id) > 128):
            raise ValueError("Invalid migration plan ID")
        if plan_id is None:
            candidates = sorted(
                self.root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
            )
            if not candidates:
                raise FileNotFoundError("No persisted migration plans are available")
            path = candidates[0]
        else:
            path = self.root / f"{plan_id}.json"
        return PersistedMigrationPlan.model_validate_json(path.read_text())


def load_planning_artifacts(repository: Path, state_directory: Path, limit: int) -> str:
    """Return bounded retained artifacts, newest first, with their source paths."""

    root = repository / state_directory
    candidates: list[Path] = []
    for relative in ("assessment.json", "assessment.md", "summary.json", "summary.md"):
        path = root / relative
        if path.is_file():
            candidates.append(path)
    for directory in ("assessments", "dependencies"):
        path = root / directory
        if path.is_dir():
            candidates.extend(item for item in path.iterdir() if item.is_file())
    candidates = sorted(set(candidates), key=lambda path: path.stat().st_mtime, reverse=True)

    sections: list[str] = []
    remaining = limit
    for path in candidates:
        if remaining <= 0:
            break
        try:
            content = path.read_text()
        except (OSError, UnicodeError):
            continue
        header = f"Artifact: {path.relative_to(repository)}\n"
        section = header + content
        sections.append(section[:remaining])
        remaining -= len(section)
    return "\n\n---\n\n".join(sections) or "No persisted project artifacts are available."
