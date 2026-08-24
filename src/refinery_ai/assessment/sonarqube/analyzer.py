"""Orchestrate one temporary local SonarQube analysis."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from refinery_ai.assessment.models import (
    AnalyzerResult,
    AnalyzerStatus,
    Diagnostic,
    ToolIdentity,
)
from refinery_ai.assessment.sonarqube.client import SonarQubeClient
from refinery_ai.assessment.sonarqube.docker import DockerOptions, SonarQubeContainer
from refinery_ai.assessment.sonarqube.models import (
    SonarQubeError,
    SonarQubeTimeout,
    SonarQubeUnavailable,
    SonarScannerContext,
)
from refinery_ai.assessment.sonarqube.normalization import (
    METRIC_KEYS,
    normalize_findings,
    normalize_metrics,
)
from refinery_ai.assessment.sonarqube.scanner import ScannerRunner
from refinery_ai.models import DiscoverySnapshot


@dataclass(frozen=True)
class SonarQubeOptions:
    image: str
    image_pull_timeout_seconds: int
    startup_timeout_seconds: int
    analysis_timeout_seconds: int
    poll_interval_seconds: float
    maven_scanner_version: str
    keep_container: bool = False
    output_limit: int = 20_000


class SonarQubeAnalyzer:
    name = "sonarqube"

    def __init__(
        self,
        assessment_id: str,
        options: SonarQubeOptions,
        *,
        container_type: type[SonarQubeContainer] = SonarQubeContainer,
        client_type: type[SonarQubeClient] = SonarQubeClient,
        scanner: ScannerRunner | None = None,
    ) -> None:
        self.assessment_id = assessment_id
        self.options = options
        self.container_type = container_type
        self.client_type = client_type
        self.scanner = scanner or ScannerRunner(
            options.maven_scanner_version,
            timeout_seconds=options.analysis_timeout_seconds,
            output_limit=options.output_limit,
        )

    def analyze(self, repository: Path, snapshot: DiscoverySnapshot) -> AnalyzerResult:
        started = time.monotonic()
        metadata: dict[str, Any] = {"containerImage": self.options.image}
        try:
            container = self.container_type(
                self.assessment_id,
                DockerOptions(
                    image=self.options.image,
                    startup_timeout_seconds=self.options.startup_timeout_seconds,
                    poll_interval_seconds=self.options.poll_interval_seconds,
                    image_pull_timeout_seconds=self.options.image_pull_timeout_seconds,
                    keep=self.options.keep_container,
                    output_limit=self.options.output_limit,
                ),
            )
            with container as server:
                metadata["containerImageDigest"] = server.image_digest
                bootstrap = self.client_type(server.url)
                bootstrap.wait_ready(
                    self.options.startup_timeout_seconds,
                    self.options.poll_interval_seconds,
                    container_running=container.running,
                )
                token = self._bootstrap_token(bootstrap)
                client = self.client_type(server.url, token=token)
                server_version = client.server_version()
                project_key = project_key_for(repository)
                task_id, scanner_identity = self.scanner.scan(
                    SonarScannerContext(repository, server.url, token, project_key), snapshot
                )
                analysis_id = client.wait_for_task(
                    task_id,
                    self.options.analysis_timeout_seconds,
                    self.options.poll_interval_seconds,
                )
                raw_metrics = client.measures(project_key, METRIC_KEYS)
                issues = client.issues(project_key)
                profiles = client.quality_profiles(project_key)
                metrics: dict[str, int | float | str | bool | None] = {}
                metrics.update(normalize_metrics(raw_metrics))
                metrics.update(
                    {
                        "serverVersion": server_version,
                        "projectKey": project_key,
                        "analysisId": analysis_id,
                    }
                )
                findings = normalize_findings(issues, project_key)
                metadata.update(
                    {
                        "serverVersion": server_version,
                        "scanner": scanner_identity,
                        "projectKey": project_key,
                        "qualityProfiles": profiles,
                        "metricSetVersion": "1",
                        "normalizationVersion": "1",
                    }
                )
                diagnostics = []
                if self.options.keep_container:
                    diagnostics.append(
                        Diagnostic(
                            level="info",
                            message=(
                                f"SonarQube container {server.container_name} was kept at "
                                f"{server.url}; remove it with docker rm --force --volumes "
                                f"{server.container_name}"
                            ),
                        )
                    )
                return AnalyzerResult(
                    analyzer=self.name,
                    status=AnalyzerStatus.COMPLETED,
                    completeness=1,
                    duration_seconds=time.monotonic() - started,
                    tool=self._identity(server_version, metadata),
                    metrics=metrics,
                    findings=findings,
                    diagnostics=diagnostics,
                )
        except SonarQubeUnavailable as error:
            return self._failure(AnalyzerStatus.UNAVAILABLE, started, error, metadata)
        except SonarQubeTimeout as error:
            return self._failure(AnalyzerStatus.TIMED_OUT, started, error, metadata)
        except SonarQubeError as error:
            return self._failure(AnalyzerStatus.FAILED, started, error, metadata)
        except Exception as error:
            sanitized = SonarQubeError(
                f"Unexpected SonarQube integration failure: {type(error).__name__}"
            )
            return self._failure(AnalyzerStatus.FAILED, started, sanitized, metadata)

    def _bootstrap_token(self, client: SonarQubeClient) -> str:
        randomized_password = secrets.token_urlsafe(32)
        password_change_error: SonarQubeError | None = None
        try:
            client.change_admin_password(randomized_password)
        except SonarQubeError as error:
            # The request may have reached the server even if its response was lost. Try both
            # possible credentials below rather than repeating a non-idempotent password change.
            password_change_error = error
        deadline = time.monotonic() + min(60, self.options.startup_timeout_seconds)
        errors: list[str] = []
        attempt = 0
        while time.monotonic() < deadline:
            for password in (randomized_password, "admin"):
                attempt += 1
                try:
                    return client.generate_token(
                        f"refinery-{self.assessment_id[:12]}-{attempt}", password
                    )
                except SonarQubeError as error:
                    errors.append(str(error))
            time.sleep(self.options.poll_interval_seconds)
        detail = errors[-1] if errors else str(password_change_error or "unknown bootstrap error")
        raise SonarQubeError(
            f"SonarQube administrator bootstrap failed after server startup: {detail}"
        ) from password_change_error

    def _failure(
        self,
        status: AnalyzerStatus,
        started: float,
        error: Exception,
        metadata: dict[str, Any],
    ) -> AnalyzerResult:
        return AnalyzerResult(
            analyzer=self.name,
            status=status,
            completeness=0,
            duration_seconds=time.monotonic() - started,
            tool=self._identity("unavailable", metadata),
            diagnostics=[Diagnostic(message=str(error))],
        )

    def _identity(self, version: str, metadata: dict[str, Any]) -> ToolIdentity:
        payload = {
            "image": self.options.image,
            "mavenScannerVersion": self.options.maven_scanner_version,
            "metadata": metadata,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return ToolIdentity(
            name=self.name,
            version=version,
            configuration_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            metadata=metadata,
        )


def project_key_for(repository: Path) -> str:
    root = repository.expanduser().resolve()
    name = "".join(
        character if character.isalnum() or character in "._-" else "-" for character in root.name
    )
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    return f"refinery:{name or 'repository'}:{digest}"
