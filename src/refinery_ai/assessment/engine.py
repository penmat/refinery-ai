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
from refinery_ai.discovery import discover


class AssessmentEngine:
    def __init__(self, analyzers: tuple[Analyzer, ...] = BUILT_IN_ANALYZERS) -> None:
        self.analyzers = analyzers

    def assess(self, repository: Path, *, name: str | None = None) -> Assessment:
        root = repository.expanduser().resolve()
        snapshot = discover(root)
        results: dict[str, AnalyzerResult] = {}
        for analyzer in self.analyzers:
            results[analyzer.name] = analyzer.analyze(root, snapshot)
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
            assessment_id=uuid4().hex,
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
