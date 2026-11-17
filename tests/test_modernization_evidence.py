import subprocess

import pytest

from refinery_ai.config import Settings
from refinery_ai.modernization.compatibility import CompatibilityProvider
from refinery_ai.modernization.evidence import collect_evidence
from refinery_ai.modernization.policy import mode_policy
from refinery_ai.modernization.versions import VersionCandidateProvider


def test_collection_obeys_mode(tmp_path):
    (tmp_path / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><properties><maven.compiler.release>17</maven.compiler.release></properties></project>"
    )
    result = collect_evidence(tmp_path, mode_policy("minimal"), Settings())
    assert all(a.status == "off" for a in result.analysis)
    assert result.snapshot.java_version == "17"


def test_compatibility_constraints():
    provider = CompatibilityProvider()
    assert provider.supports("2.7.18", 21)
    assert not provider.supports("2.7.18", 25)
    assert provider.supports("3.5.16", 21)
    assert not provider.supports("unknown", 21)


def test_version_metadata_bounds(tmp_path):
    provider = VersionCandidateProvider(
        tmp_path,
        fetch=lambda url: (
            "<metadata><versioning><versions><version>1.2.3</version><version>2.0-SNAPSHOT</version></versions></versioning></metadata>"
        ),
    )
    result = provider.candidates("g", "a")
    assert [r.version for r in result] == ["1.2.3"]
    offline = VersionCandidateProvider(
        tmp_path, fetch=lambda url: (_ for _ in ()).throw(OSError("offline"))
    )
    assert offline.candidates("g", "a")[0].source == result[0].source


def test_root_java_version_is_not_masked_by_child(tmp_path):
    from refinery_ai.config import Settings
    from refinery_ai.modernization.evidence import collect_evidence
    from refinery_ai.modernization.policy import mode_policy

    (tmp_path / "pom.xml").write_text(
        "<project><properties><maven.compiler.release>17</maven.compiler.release></properties><modules><module>child</module></modules></project>"
    )
    (tmp_path / "child").mkdir()
    (tmp_path / "child/pom.xml").write_text(
        "<project><properties><maven.compiler.release>21</maven.compiler.release></properties></project>"
    )
    observed = collect_evidence(tmp_path, mode_policy("minimal"), Settings())
    assert observed.module_versions["."]["java"] == "17"
    assert observed.module_versions["child"]["java"] == "21"


def test_explicit_spring_bom_is_a_framework_owner(tmp_path):
    from refinery_ai.config import Settings
    from refinery_ai.modernization.evidence import collect_evidence
    from refinery_ai.modernization.policy import mode_policy

    (
        tmp_path / "pom.xml"
    ).write_text("""<project><properties><java.version>17</java.version></properties>
      <dependencyManagement><dependencies><dependency>
      <groupId>org.springframework.boot</groupId><artifactId>spring-boot-dependencies</artifactId>
      <version>3.5.16</version><type>pom</type><scope>import</scope>
      </dependency></dependencies></dependencyManagement></project>""")
    observed = collect_evidence(tmp_path, mode_policy("minimal"), Settings())
    assert observed.snapshot.spring_boot_version == "3.5.16"
    assert observed.module_versions["."]["spring_boot"] == "3.5.16"


def test_full_codegraph_honors_runtime_limits_and_state_path(tmp_path, monkeypatch):
    from refinery_ai.config import Settings
    from refinery_ai.context_graph.contracts import ContextFragment
    from refinery_ai.context_graph.providers import CodeGraphProvider
    from refinery_ai.modernization.evidence import collect_evidence
    from refinery_ai.modernization.policy import mode_policy

    (tmp_path / "pom.xml").write_text(
        "<project><properties><java.version>17</java.version></properties></project>"
    )

    def collect(provider, request):
        assert provider.timeout == 7
        assert provider.output_limit == 512
        return ContextFragment(provider="codegraph", nodes=[], edges=[])

    monkeypatch.setattr(CodeGraphProvider, "collect", collect)

    class OfflineAssessment:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("optional fixture analyzer offline")

    monkeypatch.setattr("refinery_ai.modernization.evidence.AssessmentEngine", OfflineAssessment)
    # Avoid inspecting or starting a real Docker service during this fixture.
    import subprocess

    run = subprocess.run

    def commands(command, **kwargs):
        if command[0] == "docker":
            return subprocess.CompletedProcess(command, 1)
        return run(command, **kwargs)

    monkeypatch.setattr("refinery_ai.modernization.evidence.subprocess.run", commands)
    observed = collect_evidence(
        tmp_path,
        mode_policy("full"),
        Settings(codegraph_timeout_seconds=7, max_output_characters=512, state_directory="state"),
    )
    assert next(a for a in observed.analysis if a.analyzer == "codegraph").status == "completed"
    assert (tmp_path / "state/context/context-graph.json").is_file()


@pytest.mark.parametrize("mode", ["recommended", "full"])
@pytest.mark.parametrize("status", ["completed", "partial"])
def test_collection_retains_analyzer_evidence_through_real_assessment(
    tmp_path, monkeypatch, stub_codegraph, mode, status
):
    from refinery_ai.assessment.models import AnalyzerResult, Diagnostic, ToolIdentity
    from refinery_ai.inspectors.dependencies import DependencyInspector
    from refinery_ai.inspectors.sonarqube import SonarQubeInspector

    pom = "<project><properties><java.version>17</java.version></properties></project>"
    (tmp_path / "pom.xml").write_text(pom)

    # Replace external Maven/OSV and Docker analysis, keeping assessment and scoring real.
    def analyze(analyzer, repository, snapshot):
        return AnalyzerResult(
            analyzer=analyzer.name,
            status=status,
            completeness=1 if status == "completed" else 0.5,
            duration_seconds=0,
            tool=ToolIdentity(
                name=analyzer.name,
                version="test",
                configuration_hash="test",
                metadata={
                    "resolvedDependencies": [
                        {"group_id": "example", "artifact_id": "library", "version": "1.0"}
                    ],
                    "vulnerabilityEvidence": {"example:library": [{"id": "TEST-1"}]},
                }
                if analyzer.name == "dependencies"
                else {},
            ),
            diagnostics=[Diagnostic(message="Incomplete analysis")] if status == "partial" else [],
        )

    monkeypatch.setattr(DependencyInspector, "analyze", analyze)
    monkeypatch.setattr(SonarQubeInspector, "analyze", analyze)
    run = subprocess.run

    def commands(command, **kwargs):
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0)
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", commands)
    observed = collect_evidence(tmp_path, mode_policy(mode), Settings())

    availability = {a.analyzer: a.status for a in observed.analysis}
    assert availability == {
        "dependencies": status,
        "sonarqube": status if mode == "full" else "off",
        "codegraph": "completed" if mode == "full" else "off",
    }
    assert observed.inventory == [
        {"group_id": "example", "artifact_id": "library", "version": "1.0"}
    ]
    assert observed.vulnerabilities == {"example:library": [{"id": "TEST-1"}]}
    assert {ref.id for ref in observed.references} == (
        {"discovery", "dependencies", "sonarqube", "codegraph"}
        if mode == "full"
        else {"discovery", "dependencies"}
    )
    assert observed.limitations == (
        [
            f"{name}: Incomplete analysis"
            for name in (["dependencies", "sonarqube"] if mode == "full" else ["dependencies"])
        ]
        if status == "partial"
        else []
    )
    assert (tmp_path / "pom.xml").read_text() == pom
