import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from typer.testing import CliRunner

from refinery_ai.assessment.engine import AssessmentEngine
from refinery_ai.assessment.models import (
    AnalyzerResult,
    AnalyzerStatus,
    Category,
    Diagnostic,
    Finding,
    ToolIdentity,
)
from refinery_ai.assessment.scoring import score
from refinery_ai.cli import app
from refinery_ai.config import Settings
from refinery_ai.discovery import discover
from refinery_ai.inspectors import InspectionRequest
from refinery_ai.inspectors.sonarqube import (
    SonarQubeAnalyzer,
    SonarQubeInspector,
    SonarQubeOptions,
)
from refinery_ai.inspectors.sonarqube.client import SonarQubeClient
from refinery_ai.inspectors.sonarqube.docker import DockerOptions, SonarQubeContainer
from refinery_ai.inspectors.sonarqube.inspector import project_key_for
from refinery_ai.inspectors.sonarqube.models import SonarQubeError, SonarScannerContext
from refinery_ai.inspectors.sonarqube.normalization import normalize_findings, normalize_metrics
from refinery_ai.inspectors.sonarqube.scanner import ScannerRunner


def completed(
    command: list[str], code: int = 0, out: str = "", err: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, out, err)


def maven_repository(tmp_path: Path) -> Path:
    (tmp_path / "src/main/java/example").mkdir(parents=True)
    (tmp_path / "src/main/java/example/App.java").write_text("package example; class App {}")
    (tmp_path / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion>"
        "<properties><java.version>17</java.version></properties></project>"
    )
    return tmp_path


def sonar_options(**changes: Any) -> SonarQubeOptions:
    values = {
        "image": "sonarqube:26.8.0.126808-community",
        "image_pull_timeout_seconds": 20,
        "startup_timeout_seconds": 10,
        "analysis_timeout_seconds": 20,
        "poll_interval_seconds": 0.01,
        "maven_scanner_version": "5.5.0.6356",
        "gradle_scanner_version": "7.2.2.6593",
    }
    values.update(changes)
    return SonarQubeOptions(**values)


def test_container_is_loopback_only_and_always_removed() -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["docker", "port", "refinery-sonarqube-abcdef123456"]:
            return completed(command, out="127.0.0.1:49123\n")
        if "RepoDigests" in " ".join(command):
            return completed(command, out="sonarqube@sha256:abc\n")
        return completed(command, out="ok\n")

    container = SonarQubeContainer(
        "abcdef1234567890",
        DockerOptions("sonarqube:pinned", 10, 0.01),
        run=run,
    )

    with container as server:
        assert server.url == "http://127.0.0.1:49123"

    docker_run = next(command for command in commands if command[:2] == ["docker", "run"])
    assert "127.0.0.1::9000" in docker_run
    assert ["docker", "rm", "--force", "--volumes", container.name] in commands


def test_scanner_builds_pinned_maven_command_without_token(tmp_path: Path) -> None:
    repository = maven_repository(tmp_path)
    context = SonarScannerContext(repository, "http://127.0.0.1:9000", "secret", "project:key")
    runner = ScannerRunner("5.5.0.6356", "7.2.2.6593", timeout_seconds=20, output_limit=1000)

    command, identity = runner.command(context, discover(repository))

    assert command == [
        "mvn",
        "compile",
        "org.sonarsource.scanner.maven:sonar-maven-plugin:5.5.0.6356:sonar",
        "-Dsonar.projectKey=project:key",
    ]
    assert "secret" not in " ".join(command)
    assert identity == "sonar-maven-plugin:5.5.0.6356"
    assert "test" not in command


def test_scanner_uses_shell_for_non_executable_maven_wrapper(tmp_path: Path) -> None:
    repository = maven_repository(tmp_path)
    (repository / "mvnw").touch()
    context = SonarScannerContext(repository, "http://127.0.0.1:9000", "secret", "key")
    runner = ScannerRunner(
        "5.5.0.6356", "7.2.2.6593", timeout_seconds=20, output_limit=1000
    )

    command, _ = runner.command(context, discover(repository))

    assert command[:2] == ["sh", "./mvnw"]


def test_scanner_redacts_token_and_server_on_failure(tmp_path: Path) -> None:
    repository = maven_repository(tmp_path)
    context = SonarScannerContext(repository, "http://127.0.0.1:9000", "top-secret", "key")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return completed(command, 1, err="top-secret http://127.0.0.1:9000 failed")

    runner = ScannerRunner(
        "5.5.0.6356", "7.2.2.6593", timeout_seconds=20, output_limit=1000, run=run
    )

    with pytest.raises(SonarQubeError) as caught:
        runner.scan(context, discover(repository))

    assert "top-secret" not in str(caught.value)
    assert "http://127.0.0.1:9000" not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)


def test_scanner_injects_temporary_gradle_plugin_without_editing_build(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }")
    context = SonarScannerContext(tmp_path, "http://127.0.0.1:9000", "secret", "project:key")
    runner = ScannerRunner("5.5.0.6356", "7.2.2.6593", timeout_seconds=20, output_limit=1000)
    init_script = tmp_path / "temporary-sonar.init.gradle"
    runner._write_gradle_init_script(init_script)

    command, identity = runner.command(
        context,
        discover(tmp_path),
        metadata_path=tmp_path / "report-task.txt",
        gradle_init_script=init_script,
    )

    assert command[:5] == ["gradle", "--init-script", str(init_script), "classes", "sonar"]
    assert "7.2.2.6593" in init_script.read_text()
    assert "gradlePluginPortal()" in init_script.read_text()
    assert identity == "sonar-gradle-plugin:7.2.2.6593:temporary-init"
    assert (tmp_path / "build.gradle").read_text() == "plugins { id 'java' }"


class FakeResponse:
    def __init__(self, value: object) -> None:
        self.body = value if isinstance(value, str) else json.dumps(value)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode()


def test_client_paginates_issues_and_uses_bearer_authentication() -> None:
    requests: list[Any] = []

    def open_url(request: Any, **kwargs: Any) -> FakeResponse:
        requests.append(request)
        query = parse_qs(urlparse(request.full_url).query)
        page = query.get("p", ["1"])[0]
        if page == "1":
            return FakeResponse({"issues": [{"key": "one"}], "paging": {"total": 2}})
        return FakeResponse({"issues": [{"key": "two"}], "paging": {"total": 2}})

    client = SonarQubeClient("http://127.0.0.1:9000", token="secret", open_url=open_url)

    assert [issue["key"] for issue in client.issues("project")] == ["one", "two"]
    assert all(request.get_header("Authorization") == "Bearer secret" for request in requests)


def test_normalizes_metrics_and_active_findings() -> None:
    metrics = normalize_metrics({"coverage": "72.5", "ncloc": "1000", "alert_status": "OK"})
    findings = normalize_findings(
        [
            {
                "key": "issue-1",
                "rule": "java:S2259",
                "component": "project:src/App.java",
                "line": 12,
                "message": "Null may be dereferenced",
                "status": "OPEN",
                "impacts": [{"softwareQuality": "RELIABILITY", "severity": "HIGH"}],
            },
            {"key": "ignored", "status": "ACCEPTED"},
        ],
        "project",
    )

    assert metrics == {"coverage": 72.5, "ncloc": 1000, "qualityGateStatus": "OK"}
    assert len(findings) == 1
    assert findings[0].category is Category.RISK
    assert findings[0].fingerprint.startswith("sonarqube:")
    assert "issue-1" not in findings[0].fingerprint
    assert findings[0].path == "src/App.java"
    same_finding = normalize_findings(
        [
            {
                "key": "new-ephemeral-id",
                "rule": "java:S2259",
                "component": "project:src/App.java",
                "line": 12,
                "message": "Null may be dereferenced",
                "status": "OPEN",
                "impacts": [{"softwareQuality": "RELIABILITY", "severity": "HIGH"}],
            }
        ],
        "project",
    )[0]
    assert same_finding.fingerprint == findings[0].fingerprint


def test_sonarqube_evidence_scores_maintainability_risk_and_tests(tmp_path: Path) -> None:
    repository = maven_repository(tmp_path)
    assessment = AssessmentEngine().assess(repository)
    assessment.analyzers["sonarqube"] = AnalyzerResult(
        analyzer="sonarqube",
        status=AnalyzerStatus.COMPLETED,
        completeness=1,
        duration_seconds=1,
        tool=ToolIdentity(name="sonarqube", version="1", configuration_hash="x"),
        metrics={
            "ncloc": 1000,
            "coverage": 70,
            "branchCoverage": 50,
            "cognitiveComplexity": 100,
            "duplicatedLinesDensity": 10,
        },
        findings=[
            Finding(
                fingerprint="sonarqube:one",
                analyzer="sonarqube",
                rule="java:S1",
                category=Category.RISK,
                severity="high",
                message="Risk",
            )
        ],
    )

    result = score(assessment.analyzers)

    assert result.categories[Category.MAINTAINABILITY] == 57.5
    assert result.categories[Category.RISK] == 96
    assert result.categories[Category.TESTS] == 62


def test_project_key_is_stable_and_hides_full_path(tmp_path: Path) -> None:
    first = project_key_for(tmp_path)
    assert first == project_key_for(tmp_path)
    assert str(tmp_path) not in first


def test_sonarqube_inspector_uses_shared_inspection_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = discover(tmp_path)
    expected = AnalyzerResult(
        analyzer="sonarqube",
        status=AnalyzerStatus.UNAVAILABLE,
        completeness=0,
        duration_seconds=0,
        tool=ToolIdentity(name="sonarqube", version="none", configuration_hash="x"),
    )
    inspector = SonarQubeInspector("abcdef123456", sonar_options())

    def analyze(repository: Path, received_snapshot: object) -> AnalyzerResult:
        assert repository == tmp_path.resolve()
        assert received_snapshot is snapshot
        return expected

    monkeypatch.setattr(inspector, "analyze", analyze)

    result = inspector.inspect(InspectionRequest(repository=tmp_path.resolve(), snapshot=snapshot))

    assert result is expected


def test_bootstrap_falls_back_when_password_change_response_is_uncertain() -> None:
    class BootstrapClient:
        def __init__(self) -> None:
            self.passwords: list[str] = []

        def change_admin_password(self, new_password: str) -> None:
            raise SonarQubeError("HTTP 503")

        def generate_token(self, name: str, admin_password: str) -> str:
            self.passwords.append(admin_password)
            if admin_password == "admin":
                return "temporary-token"
            raise SonarQubeError("HTTP 401")

    client = BootstrapClient()
    analyzer = SonarQubeAnalyzer("abcdef123456", sonar_options())

    assert analyzer._bootstrap_token(client) == "temporary-token"  # type: ignore[arg-type]
    assert client.passwords[-1] == "admin"


def test_cli_defaults_on_and_no_sonarqube_skips_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_codegraph: None
) -> None:
    repository = maven_repository(tmp_path)
    monkeypatch.setenv("REFINERY_DEPENDENCY_ANALYSIS_ENABLED", "false")
    calls = 0

    def unavailable(self: SonarQubeAnalyzer, repository: Path, snapshot: object) -> AnalyzerResult:
        nonlocal calls
        calls += 1
        return AnalyzerResult(
            analyzer="sonarqube",
            status=AnalyzerStatus.UNAVAILABLE,
            completeness=0,
            duration_seconds=0,
            tool=ToolIdentity(name="sonarqube", version="none", configuration_hash="x"),
            diagnostics=[Diagnostic(message="Docker unavailable")],
        )

    monkeypatch.setattr(SonarQubeAnalyzer, "analyze", unavailable)
    runner = CliRunner()

    default = runner.invoke(app, ["analyze", str(repository)])
    assert default.exit_code == 0
    assert "SonarQube: unavailable" in (repository / ".refinery/assessment.md").read_text()

    disabled = runner.invoke(app, ["analyze", str(repository), "--no-sonarqube"])
    assert disabled.exit_code == 0
    assert calls == 1

    monkeypatch.setenv("REFINERY_SONARQUBE_ENABLED", "false")
    configured_off = runner.invoke(app, ["analyze", str(repository)])
    assert configured_off.exit_code == 0
    assert calls == 1


@pytest.mark.sonarqube
@pytest.mark.skipif(
    os.environ.get("REFINERY_RUN_SONARQUBE_INTEGRATION") != "1",
    reason="set REFINERY_RUN_SONARQUBE_INTEGRATION=1 to run Docker integration",
)
def test_real_temporary_sonarqube_analysis(tmp_path: Path) -> None:
    repository = tmp_path / "legacy-java"
    shutil.copytree(Path(__file__).parent / "fixtures/legacy-java", repository)
    settings = Settings(
        sonarqube_startup_timeout_seconds=180,
        sonarqube_analysis_timeout_seconds=900,
    )

    assessment = AssessmentEngine(sonarqube_enabled=True, settings=settings).assess(repository)

    sonar = assessment.analyzers["sonarqube"]
    assert sonar.status is AnalyzerStatus.COMPLETED, sonar.diagnostics
    assert sonar.metrics["serverVersion"] == "26.8.0.126808"
    assert float(sonar.metrics["ncloc"]) > 0
    assert assessment.completeness >= 0.55
