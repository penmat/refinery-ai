import subprocess
from pathlib import Path

import pytest

from refinery_ai.discovery import discover, resolve_parent_java_version
from refinery_ai.models import BuildSystem


def test_discovers_maven_repository(tmp_path: Path) -> None:
    (tmp_path / "src/main/java").mkdir(parents=True)
    (tmp_path / "src/main/java/App.java").write_text("class App {}")
    (tmp_path / "pom.xml").write_text(
        """<project xmlns="http://maven.apache.org/POM/4.0.0">
        <modelVersion>4.0.0</modelVersion><groupId>example</groupId><artifactId>demo</artifactId>
        <version>1</version><properties><java.version>17</java.version></properties>
        <dependencies><dependency><groupId>org.springframework</groupId>
        <artifactId>spring-core</artifactId><version>6</version></dependency>
        </dependencies></project>"""
    )

    result = discover(tmp_path)

    assert result.build_system is BuildSystem.MAVEN
    assert result.java_version == "17"
    assert result.modules[0].java_files == 1
    assert result.frameworks == ["Spring"]


def test_discovers_repository_beneath_ignored_parent_directory(tmp_path: Path) -> None:
    repository = tmp_path / ".refinery/corpus-runs/run/repositories/example"
    repository.mkdir(parents=True)
    (repository / "pom.xml").write_text(
        """<project><modelVersion>4.0.0</modelVersion>
        <properties><java.version>8</java.version></properties></project>"""
    )
    ignored = repository / ".refinery/copied-project"
    ignored.mkdir(parents=True)
    (ignored / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion></project>"
    )

    result = discover(repository)

    assert result.build_system is BuildSystem.MAVEN
    assert result.build_files == [Path("pom.xml")]
    assert result.java_version == "8"


def test_discovers_spring_boot_parent_version(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        """<project><modelVersion>4.0.0</modelVersion><parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>2.1.6.RELEASE</version><relativePath/>
        </parent><properties><java.version>11</java.version></properties></project>"""
    )

    result = discover(tmp_path)

    assert result.java_version == "11"
    assert result.spring_boot_version == "2.1.6.RELEASE"


def test_discovers_non_utf8_application_properties(tmp_path: Path) -> None:
    resources = tmp_path / "src/main/resources"
    resources.mkdir(parents=True)
    (resources / "application.properties").write_bytes(
        "# Interface Gráfica\n".encode("iso-8859-1")
    )
    (resources / "application-test.properties").write_text("feature.enabled=true\n")
    (tmp_path / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion></project>"
    )

    result = discover(tmp_path)

    assert result.non_utf8_application_properties == [
        Path("src/main/resources/application.properties")
    ]


def test_discovers_data_sql_that_must_wait_for_hibernate(tmp_path: Path) -> None:
    resources = tmp_path / "src/main/resources"
    resources.mkdir(parents=True)
    (resources / "data.sql").write_text("insert into usuario values (1);\n")
    (resources / "application.properties").write_text("spring.jpa.show-sql=true\n")
    (tmp_path / "pom.xml").write_text(
        """<project><modelVersion>4.0.0</modelVersion><dependencies><dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
        </dependency></dependencies></project>"""
    )

    result = discover(tmp_path)

    assert result.deferred_datasource_initialization_properties == [
        Path("src/main/resources/application.properties")
    ]


def test_discovers_gradle_repository(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text("plugins { java }")
    assert discover(tmp_path).build_system is BuildSystem.GRADLE


def test_resolves_java_version_from_maven_central_parent(tmp_path: Path) -> None:
    child = """<project><modelVersion>4.0.0</modelVersion><parent>
      <groupId>example.parents</groupId>
      <artifactId>application-parent</artifactId><version>1.2.3</version>
      <relativePath/>
    </parent></project>"""
    parent = """<project><modelVersion>4.0.0</modelVersion>
      <properties><java.version>1.8</java.version></properties></project>"""
    calls: list[tuple[str, str, str]] = []

    def fetch(group: str, artifact: str, version: str) -> str:
        calls.append((group, artifact, version))
        return parent

    version = resolve_parent_java_version(
        tmp_path / "pom.xml", child, fetch_parent=fetch
    )

    assert version == "8"
    assert calls == [("example.parents", "application-parent", "1.2.3")]


def test_root_gradle_build_wins_over_nested_maven_examples(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }")
    (tmp_path / "settings.gradle").write_text("rootProject.name = 'application'")
    example = tmp_path / "examples/quickstart"
    example.mkdir(parents=True)
    (example / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")

    result = discover(tmp_path)

    assert result.build_system is BuildSystem.GRADLE
    assert result.build_files == [Path("build.gradle")]


def test_rejects_missing_repository(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        discover(tmp_path / "missing")


def test_ignores_refinery_state_when_checking_worktree(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".refinery").mkdir()
    (tmp_path / ".refinery" / "context.db").touch()

    assert not discover(tmp_path).dirty
