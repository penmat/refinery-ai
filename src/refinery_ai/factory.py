"""Modernization factory orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from refinery_ai.config import Settings
from refinery_ai.knowledge import KnowledgeGraphProvider
from refinery_ai.models import (
    ModernizationGoal,
    RunReport,
    RunStatus,
    StepResult,
    StepStrategy,
    VerificationResult,
)
from refinery_ai.openrewrite import OpenRewriteAdapter
from refinery_ai.planning import rule_based_plan, validate_plan
from refinery_ai.reporting import to_markdown
from refinery_ai.repository import LegacyRepositoryEnvironment, RepositoryEnvironment


class ModernizationFactory:
    """Own all control flow, effects, validation, and safety limits."""

    def __init__(
        self,
        settings: Settings | None = None,
        rewrite: OpenRewriteAdapter | None = None,
        knowledge: KnowledgeGraphProvider | None = None,
        environment: RepositoryEnvironment | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.environment = environment or LegacyRepositoryEnvironment(
            knowledge=knowledge,
            rewrite=rewrite,
            graphify_timeout=self.settings.graphify_timeout_seconds,
            output_limit=self.settings.max_output_characters,
        )

    def prepare(self, repository: Path, goal: ModernizationGoal) -> RunReport:
        snapshot = self.environment.discover(repository)
        state_dir = snapshot.repository / self.settings.state_directory
        state_dir.mkdir(parents=True, exist_ok=True)
        graph = self.environment.build_knowledge(snapshot.repository)
        plan = rule_based_plan(snapshot, goal)
        validate_plan(plan)
        report = RunReport(
            run_id=uuid4().hex,
            status=RunStatus.PLANNED,
            snapshot=snapshot,
            knowledge_graph=graph,
            plan=plan,
        )
        self._save(report)
        return report

    def run(self, repository: Path, goal: ModernizationGoal, *, apply: bool = False) -> RunReport:
        """Run approved non-source-changing steps and stop at explicit review gates."""

        report = self.prepare(repository, goal)
        if report.snapshot.dirty and not self.settings.allow_dirty_worktree:
            report.status = RunStatus.NEEDS_REVIEW
            report.steps.append(
                StepResult(
                    step_id="safety",
                    status=RunStatus.NEEDS_REVIEW,
                    message="Working tree is dirty; commit or stash changes before apply mode.",
                )
            )
            self._save(report)
            return report
        if not apply:
            return report

        report.status = RunStatus.RUNNING
        build_command = self.environment.verification_command(report.snapshot)
        for step in report.plan.steps:
            if step.strategy is StepStrategy.COMMAND:
                if build_command is None:
                    self._fail(report, step.id, "Unknown build system")
                    break
                evidence = self.environment.verify(
                    report.snapshot.repository,
                    build_command,
                    timeout=self.settings.command_timeout_seconds,
                    output_limit=self.settings.max_output_characters,
                )
                if not evidence.green:
                    self._fail(report, step.id, "Baseline verification failed", evidence)
                    break
                report.steps.append(
                    StepResult(
                        step_id=step.id,
                        status=RunStatus.COMPLETED,
                        message="Baseline verification passed",
                        verification=evidence,
                    )
                )
            elif step.strategy is StepStrategy.OPENREWRITE:
                if not step.recipe:
                    self._review(report, step.id, "No approved recipe matches this target")
                    break
                evidence = self.environment.dry_run_rewrite(
                    report.snapshot,
                    step.recipe,
                    timeout=self.settings.command_timeout_seconds,
                )
                if not evidence.green:
                    self._fail(report, step.id, "OpenRewrite dry run failed", evidence)
                    break
                self._review(
                    report,
                    step.id,
                    "OpenRewrite dry run passed; review its patch before apply",
                )
                report.steps[-1].verification = evidence
                break
            else:
                self._review(
                    report,
                    step.id,
                    f"{step.strategy.value} source changes require explicit review in v0.1",
                )
                break
        else:
            report.status = RunStatus.COMPLETED
        self._save(report)
        return report

    @staticmethod
    def _review(report: RunReport, step_id: str, message: str) -> None:
        report.status = RunStatus.NEEDS_REVIEW
        report.steps.append(
            StepResult(step_id=step_id, status=RunStatus.NEEDS_REVIEW, message=message)
        )

    @staticmethod
    def _fail(
        report: RunReport,
        step_id: str,
        message: str,
        evidence: VerificationResult | None = None,
    ) -> None:
        report.status = RunStatus.FAILED
        report.steps.append(
            StepResult(
                step_id=step_id,
                status=RunStatus.FAILED,
                message=message,
                verification=evidence,
            )
        )

    def _save(self, report: RunReport) -> None:
        state_dir = report.snapshot.repository / self.settings.state_directory / "runs"
        state_dir.mkdir(parents=True, exist_ok=True)
        stem = state_dir / report.run_id
        stem.with_suffix(".json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
        )
        stem.with_suffix(".md").write_text(to_markdown(report))
