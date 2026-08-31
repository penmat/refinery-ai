import json
import ssl
import subprocess
from pathlib import Path
from typing import Any

import pytest

from refinery_ai.assessment.dependency_artifacts import write_dependency_artifact
from refinery_ai.assessment.engine import AssessmentEngine
from refinery_ai.assessment.models import AnalyzerStatus, Category
from refinery_ai.assessment.scoring import score
from refinery_ai.discovery import discover
from refinery_ai.inspectors import InspectionRequest
from refinery_ai.inspectors.dependencies import (
    DependencyAnalyzer,
    DependencyAnalyzerOptions,
    DependencyInspector,
)
from refinery_ai.inspectors.dependencies.collectors import ResolvedDependencyCollector
from refinery_ai.inspectors.dependencies.models import (
    DependencyAnalysisError,
    ResolvedDependency,
    Vulnerability,
)
from refinery_ai.inspectors.dependencies.osv import OsvClient, _cvss3
from refinery_ai.inspectors.dependencies.purl import maven_purl
from refinery_ai.models import BuildSystem


def options(**changes: Any) -> DependencyAnalyzerOptions:
    values: dict[str, Any] = {
        "maven_plugin_version": "3.11.0",
        "resolution_timeout_seconds": 20,
        "osv_url": "https://api.osv.dev",
        "osv_timeout_seconds": 10,
        "osv_batch_size": 1000,
    }
    values.update(changes)
    return DependencyAnalyzerOptions(
        **values,
    )


class FakeCollector:
    def __init__(self, dependencies: list[ResolvedDependency]) -> None:
        self.dependencies = dependencies

    def collect(self, repository: Path, build_system: BuildSystem) -> list[ResolvedDependency]:
        return self.dependencies


class FakeOsv:
    def __init__(self, results: dict[str, list[Vulnerability]]) -> None:
        self.results = results

    def query(self, purls: list[str]) -> dict[str, list[Vulnerability]]:
        return {purl: self.results.get(purl, []) for purl in purls}


def test_maven_purl_is_canonical_and_versioned() -> None:
    dependency = ResolvedDependency("org.example", "demo library", "1.2.3+build")

    assert maven_purl(dependency) == "pkg:maven/org.example/demo%20library@1.2.3%2Bbuild"


def test_maven_collector_reads_resolved_transitive_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "-DappendOutput=true" in command
        output_argument = next(item for item in command if item.startswith("-DoutputFile="))
        output = Path(output_argument.split("=", 1)[1])
        output.write_text(
            json.dumps(
                {
                    "groupId": "app",
                    "artifactId": "root",
                    "version": "1",
                    "children": [
                        {
                            "groupId": "org.example",
                            "artifactId": "direct",
                            "version": "2",
                            "scope": "compile",
                            "children": [
                                {
                                    "groupId": "org.example",
                                    "artifactId": "transitive",
                                    "version": "3",
                                    "scope": "runtime",
                                }
                            ],
                        }
                    ],
                }
            )
            + json.dumps(
                {
                    "groupId": "app",
                    "artifactId": "module",
                    "version": "1",
                    "children": [],
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    dependencies = ResolvedDependencyCollector("3.11.0", 20, 1000).collect(
        tmp_path, BuildSystem.MAVEN
    )

    assert [item.coordinate for item in dependencies] == [
        "org.example:direct:2",
        "org.example:transitive:3",
    ]
    assert dependencies[0].direct is True
    assert dependencies[1].direct is False


def test_maven_collector_retries_old_wrapper_without_transfer_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "--no-transfer-progress" in command:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "Unable to parse command line options: "
                "Unrecognized option: --no-transfer-progress",
            )
        output_argument = next(item for item in command if item.startswith("-DoutputFile="))
        Path(output_argument.split("=", 1)[1]).write_text(
            json.dumps(
                {
                    "groupId": "app",
                    "artifactId": "root",
                    "version": "1",
                    "children": [
                        {
                            "groupId": "org.example",
                            "artifactId": "resolved",
                            "version": "2",
                        }
                    ],
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)

    dependencies = ResolvedDependencyCollector("3.11.0", 20, 1000).collect(
        tmp_path, BuildSystem.MAVEN
    )

    assert dependencies[0].coordinate == "org.example:resolved:2"
    assert len(commands) == 2
    assert "--no-transfer-progress" in commands[0]
    assert "--no-transfer-progress" not in commands[1]


def test_gradle_collector_uses_temporary_resolution_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        init_script = Path(command[command.index("--init-script") + 1])
        assert "resolutionResult.allComponents" in init_script.read_text()
        output_argument = next(
            item for item in command if item.startswith("-Drefinery.dependencies.output=")
        )
        Path(output_argument.split("=", 1)[1]).write_text(
            json.dumps(
                [
                    {
                        "group": "org.example",
                        "artifact": "resolved",
                        "version": "4.0",
                        "scope": "runtimeClasspath",
                        "direct": True,
                    }
                ]
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    dependencies = ResolvedDependencyCollector("3.11.0", 20, 1000).collect(
        tmp_path, BuildSystem.GRADLE
    )

    assert dependencies[0].coordinate == "org.example:resolved:4.0"
    assert (tmp_path / "build.gradle").read_text() == "plugins { id 'java' }"


def test_dependency_analyzer_emits_osv_findings(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")
    dependency = ResolvedDependency("org.example", "library", "1.0", direct=True)
    purl = maven_purl(dependency)
    analyzer = DependencyAnalyzer(
        options(),
        collector=FakeCollector([dependency]),  # type: ignore[arg-type]
        osv=FakeOsv(  # type: ignore[arg-type]
            {purl: [Vulnerability("GHSA-example", "2026-01-01T00:00:00Z", "Example issue", "high")]}
        ),
    )

    result = analyzer.analyze(tmp_path, discover(tmp_path))

    assert result.status is AnalyzerStatus.COMPLETED
    assert result.metrics["dependencyCount"] == 1
    assert result.metrics["highVulnerabilityCount"] == 1
    assert result.findings[0].fingerprint == f"osv:GHSA-example:{purl}"
    assert result.findings[0].category is Category.DEPENDENCIES
    base = AssessmentEngine().assess(tmp_path)
    base.analyzers["dependencies"] = result
    assert score(base.analyzers).categories[Category.DEPENDENCIES] == 90


def test_dependency_inspector_uses_shared_inspection_request(tmp_path: Path) -> None:
    snapshot = discover(tmp_path)
    inspector = DependencyInspector(
        options(),
        collector=FakeCollector([]),  # type: ignore[arg-type]
        osv=FakeOsv({}),  # type: ignore[arg-type]
    )

    result = inspector.inspect(InspectionRequest(repository=tmp_path.resolve(), snapshot=snapshot))

    assert result.analyzer == "dependencies"
    assert result.status is AnalyzerStatus.COMPLETED


def test_dependency_resolution_failure_is_unavailable(tmp_path: Path) -> None:
    class BrokenCollector:
        def collect(self, repository: Path, build_system: BuildSystem) -> list[ResolvedDependency]:
            raise DependencyAnalysisError("resolution failed")

    analyzer = DependencyAnalyzer(
        options(),
        collector=BrokenCollector(),  # type: ignore[arg-type]
        osv=FakeOsv({}),  # type: ignore[arg-type]
    )

    result = analyzer.analyze(tmp_path, discover(tmp_path))

    assert result.status is AnalyzerStatus.UNAVAILABLE
    assert result.completeness == 0
    assert "resolution failed" in result.diagnostics[0].message


def test_cvss3_base_score() -> None:
    assert _cvss3("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8


def test_osv_tls_verification_can_be_explicitly_disabled() -> None:
    verified = OsvClient("https://api.osv.dev", 10)
    insecure = OsvClient("https://api.osv.dev", 10, verify_ssl=False)

    assert verified.ssl_context.verify_mode is ssl.CERT_REQUIRED
    assert verified.ssl_context.check_hostname is True
    assert insecure.ssl_context.verify_mode is ssl.CERT_NONE
    assert insecure.ssl_context.check_hostname is False


def test_disabled_osv_tls_verification_is_recorded(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")
    analyzer = DependencyAnalyzer(
        options(osv_verify_ssl=False),
        collector=FakeCollector([]),  # type: ignore[arg-type]
        osv=FakeOsv({}),  # type: ignore[arg-type]
    )

    result = analyzer.analyze(tmp_path, discover(tmp_path))

    assert result.status is AnalyzerStatus.COMPLETED
    assert result.tool.metadata["osvTlsVerification"] is False
    assert "verification was disabled" in result.diagnostics[0].message


def test_dependency_artifact_preserves_full_structured_findings(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")
    assessment = AssessmentEngine().assess(tmp_path)
    dependency = ResolvedDependency("org.example", "library", "1.0")
    purl = maven_purl(dependency)
    result = DependencyAnalyzer(
        options(),
        collector=FakeCollector([dependency]),  # type: ignore[arg-type]
        osv=FakeOsv(  # type: ignore[arg-type]
            {purl: [Vulnerability("GHSA-example", None, "Detailed issue", "high")]}
        ),
    ).analyze(tmp_path, discover(tmp_path))
    assessment.analyzers["dependencies"] = result
    assessment.metrics["dependencies"] = dict(result.metrics)

    path = write_dependency_artifact(assessment)

    assert path is not None and path.is_file()
    payload = json.loads(path.read_text())
    assert payload["findings"][0]["message"].endswith("Detailed issue")
    assert result.metrics["reportPath"] == str(path.relative_to(tmp_path))
