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
