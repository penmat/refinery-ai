"""Assessment orchestration without model-generated measurements."""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

from refinery_ai.assessment.models import (
    AnalyzerResult,
    Assessment,
    AssessmentScope,
    RepositoryIdentity,
)
from refinery_ai.assessment.scoring import make_profile, score
from refinery_ai.config import Settings
from refinery_ai.inspectors import (
    BUILT_IN_INSPECTORS,
    InspectionRunner,
    Inspector,
    InspectorRegistry,
)
from refinery_ai.inspectors.dependencies import DependencyAnalyzerOptions, DependencyInspector
from refinery_ai.inspectors.sonarqube import SonarQubeInspector, SonarQubeOptions
from refinery_ai.logging import get_logger, log_command

logger = get_logger(__name__)


class AssessmentEngine:
    def __init__(
        self,
        inspectors: tuple[Inspector[AnalyzerResult], ...] = BUILT_IN_INSPECTORS,
        *,
        dependency_analysis_enabled: bool = False,
        sonarqube_enabled: bool = False,
        keep_sonarqube: bool = False,
        settings: Settings | None = None,
    ) -> None:
        self.inspectors = inspectors
        self.dependency_analysis_enabled = dependency_analysis_enabled
        self.sonarqube_enabled = sonarqube_enabled
        self.keep_sonarqube = keep_sonarqube
        self.settings = settings or Settings()

    def assess(self, repository: Path, *, name: str | None = None) -> Assessment:
        root = repository.expanduser().resolve()
        assessment_id = uuid4().hex
        inspectors = list(self.inspectors)
        if self.dependency_analysis_enabled:
            inspectors.append(
                DependencyInspector(
                    DependencyAnalyzerOptions(
                        maven_plugin_version=self.settings.maven_dependency_plugin_version,
                        resolution_timeout_seconds=(
                            self.settings.dependency_resolution_timeout_seconds
                        ),
                        osv_url=self.settings.osv_api_url,
                        osv_timeout_seconds=self.settings.osv_timeout_seconds,
                        osv_batch_size=self.settings.osv_batch_size,
                        osv_verify_ssl=self.settings.osv_verify_ssl,
                        output_limit=self.settings.max_output_characters,
                    )
                )
            )
        if self.sonarqube_enabled:
            inspectors.append(
                SonarQubeInspector(
                    assessment_id,
                    SonarQubeOptions(
                        image=self.settings.sonarqube_image,
                        image_pull_timeout_seconds=(
                            self.settings.sonarqube_image_pull_timeout_seconds
                        ),
                        startup_timeout_seconds=self.settings.sonarqube_startup_timeout_seconds,
                        analysis_timeout_seconds=self.settings.sonarqube_analysis_timeout_seconds,
                        poll_interval_seconds=self.settings.sonarqube_poll_interval_seconds,
                        maven_scanner_version=self.settings.sonarqube_maven_scanner_version,
                        gradle_scanner_version=self.settings.sonarqube_gradle_scanner_version,
                        keep_container=self.keep_sonarqube,
                        output_limit=self.settings.max_output_characters,
                    ),
                )
            )
        registry = InspectorRegistry(inspectors)
        inspection = InspectionRunner(registry).inspect(root)
        snapshot = inspection.request.snapshot
        results: dict[str, AnalyzerResult] = dict(inspection.results)
        metrics = {
            analyzer_name: dict(result.metrics) for analyzer_name, result in sorted(results.items())
        }
        findings = [finding for result in results.values() for finding in result.findings]
        assessment_score = score(results)
        profile = make_profile([result.tool for result in results.values()])
        measured_weight = sum(
            profile.category_weights[category]
            for category, value in assessment_score.categories.items()
            if value is not None
        )
        total_weight = sum(profile.category_weights.values())
        return Assessment(
            assessment_id=assessment_id,
            name=name,
            repository=RepositoryIdentity(
                root=root,
                commit=_git(root, "rev-parse", "HEAD"),
                branch=snapshot.git_branch,
                dirty=snapshot.dirty,
            ),
            profile=profile,
            scope=AssessmentScope(
                modules_included=[str(module.path) for module in snapshot.modules]
            ),
            completeness=round(measured_weight / total_weight, 4) if total_weight else 0,
            score=assessment_score,
            metrics=metrics,
            findings=findings,
            analyzers=results,
        )


def _git(root: Path, *args: str) -> str | None:
    command = ["git", *args]
    log_command(logger, command, cwd=root)
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None
