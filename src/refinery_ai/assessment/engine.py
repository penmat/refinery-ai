"""Assessment orchestration without model-generated measurements."""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

from refinery_ai.assessment.analyzers import BUILT_IN_ANALYZERS, Analyzer
from refinery_ai.assessment.models import (
    AnalyzerResult,
    Assessment,
    AssessmentScope,
    RepositoryIdentity,
)
from refinery_ai.assessment.scoring import make_profile, score
from refinery_ai.assessment.sonarqube import SonarQubeAnalyzer, SonarQubeOptions
from refinery_ai.config import Settings
from refinery_ai.discovery import discover


class AssessmentEngine:
    def __init__(
        self,
        analyzers: tuple[Analyzer, ...] = BUILT_IN_ANALYZERS,
        *,
        sonarqube_enabled: bool = False,
        keep_sonarqube: bool = False,
        settings: Settings | None = None,
    ) -> None:
        self.analyzers = analyzers
        self.sonarqube_enabled = sonarqube_enabled
        self.keep_sonarqube = keep_sonarqube
        self.settings = settings or Settings()

    def assess(self, repository: Path, *, name: str | None = None) -> Assessment:
        root = repository.expanduser().resolve()
        snapshot = discover(root)
        assessment_id = uuid4().hex
        results: dict[str, AnalyzerResult] = {}
        for analyzer in self.analyzers:
            results[analyzer.name] = analyzer.analyze(root, snapshot)
        if self.sonarqube_enabled:
            sonar = SonarQubeAnalyzer(
                assessment_id,
                SonarQubeOptions(
                    image=self.settings.sonarqube_image,
                    image_pull_timeout_seconds=self.settings.sonarqube_image_pull_timeout_seconds,
                    startup_timeout_seconds=self.settings.sonarqube_startup_timeout_seconds,
                    analysis_timeout_seconds=self.settings.sonarqube_analysis_timeout_seconds,
                    poll_interval_seconds=self.settings.sonarqube_poll_interval_seconds,
                    maven_scanner_version=self.settings.sonarqube_maven_scanner_version,
                    gradle_scanner_version=self.settings.sonarqube_gradle_scanner_version,
                    keep_container=self.keep_sonarqube,
                    output_limit=self.settings.max_output_characters,
                ),
            )
            results[sonar.name] = sonar.analyze(root, snapshot)
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
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None
