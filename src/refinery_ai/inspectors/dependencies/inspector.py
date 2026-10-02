"""Resolved dependency and vulnerability inspection orchestration."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from refinery_ai.assessment.models import (
    AnalyzerResult,
    AnalyzerStatus,
    Category,
    Diagnostic,
    Finding,
    ToolIdentity,
)
from refinery_ai.build_tools import build_directory
from refinery_ai.inspectors.contracts import InspectionRequest
from refinery_ai.inspectors.dependencies.collectors import ResolvedDependencyCollector
from refinery_ai.inspectors.dependencies.models import (
    DependencyAnalysisError,
    DependencyAnalysisTimeout,
)
from refinery_ai.inspectors.dependencies.osv import OsvClient
from refinery_ai.inspectors.dependencies.purl import maven_purl
from refinery_ai.models import DiscoverySnapshot


@dataclass(frozen=True)
class DependencyAnalyzerOptions:
    maven_plugin_version: str
    resolution_timeout_seconds: int
    osv_url: str
    osv_timeout_seconds: int
    osv_batch_size: int
    osv_verify_ssl: bool = True
    output_limit: int = 20_000


class DependencyAnalyzer:
    name = "dependencies"

    def __init__(
        self,
        options: DependencyAnalyzerOptions,
        *,
        collector: ResolvedDependencyCollector | None = None,
        osv: OsvClient | None = None,
    ) -> None:
        self.options = options
        self.collector = collector or ResolvedDependencyCollector(
            options.maven_plugin_version,
            options.resolution_timeout_seconds,
            options.output_limit,
        )
        self.osv = osv or OsvClient(
            options.osv_url,
            options.osv_timeout_seconds,
            options.osv_batch_size,
            verify_ssl=options.osv_verify_ssl,
        )

    def analyze(self, repository: Path, snapshot: DiscoverySnapshot) -> AnalyzerResult:
        started = time.monotonic()
        metadata: dict[str, Any] = {
            "buildSystem": snapshot.build_system.value,
            "mavenDependencyPluginVersion": self.options.maven_plugin_version,
            "osvApi": self.options.osv_url,
            "osvTlsVerification": self.options.osv_verify_ssl,
            "normalizationVersion": "1",
        }
        try:
            dependencies = self.collector.collect(
                repository / build_directory(snapshot.build_files), snapshot.build_system
            )
            purls = [maven_purl(dependency) for dependency in dependencies]
            vulnerabilities = self.osv.query(purls)
        except DependencyAnalysisTimeout as error:
            return self._failure(AnalyzerStatus.TIMED_OUT, started, error, metadata)
        except DependencyAnalysisError as error:
            return self._failure(AnalyzerStatus.UNAVAILABLE, started, error, metadata)
        findings = []
        by_purl = dict(zip(purls, dependencies, strict=True))
        latest_revision: str | None = None
        vulnerable_purls: set[str] = set()
        for purl, items in vulnerabilities.items():
            dependency = by_purl[purl]
            for vulnerability in items:
                vulnerable_purls.add(purl)
                latest_revision = (
                    max(latest_revision or "", vulnerability.modified or "") or latest_revision
                )
                findings.append(
                    Finding(
                        fingerprint=f"osv:{vulnerability.id}:{purl}",
                        analyzer=self.name,
                        rule=vulnerability.id,
                        category=Category.DEPENDENCIES,
                        severity=vulnerability.severity,
                        message=(
                            f"{dependency.coordinate} is affected by {vulnerability.id}"
                            + (f": {vulnerability.summary}" if vulnerability.summary else "")
                        ),
                    )
                )
        metadata["osvDatabaseRevision"] = latest_revision
        counts = {
            severity: sum(item.severity == severity for item in findings)
            for severity in ("critical", "high", "medium", "low", "unknown")
        }
        metrics: dict[str, int | float | str | bool | None] = {
            "dependencyCount": len(dependencies),
            "directDependencyCount": sum(item.direct for item in dependencies),
            "vulnerableDependencyCount": len(vulnerable_purls),
            "vulnerabilityCount": len(findings),
            **{f"{severity}VulnerabilityCount": count for severity, count in counts.items()},
        }
        diagnostics = []
        if not self.options.osv_verify_ssl:
            diagnostics.append(
                Diagnostic(
                    level="warning",
                    message="OSV TLS certificate verification was disabled for this assessment.",
                )
            )
        return AnalyzerResult(
            analyzer=self.name,
            status=AnalyzerStatus.COMPLETED,
            completeness=1,
            duration_seconds=time.monotonic() - started,
            tool=self._identity(metadata),
            metrics=metrics,
            findings=findings,
            diagnostics=diagnostics,
        )

    def _failure(
        self, status: AnalyzerStatus, started: float, error: Exception, metadata: dict[str, Any]
    ) -> AnalyzerResult:
        return AnalyzerResult(
            analyzer=self.name,
            status=status,
            completeness=0,
            duration_seconds=time.monotonic() - started,
            tool=self._identity(metadata),
            diagnostics=[Diagnostic(message=str(error))],
        )

    def _identity(self, metadata: dict[str, Any]) -> ToolIdentity:
        canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        return ToolIdentity(
            name=self.name,
            version="1.0",
            configuration_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            metadata=metadata,
        )


class DependencyInspector(DependencyAnalyzer):
    """Dependency and vulnerability inspector entry point."""

    def inspect(self, request: InspectionRequest) -> AnalyzerResult:
        return self.analyze(request.repository, request.snapshot)
