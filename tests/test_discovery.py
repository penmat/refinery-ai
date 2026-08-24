import subprocess
from pathlib import Path

import pytest

from refinery_ai.discovery import discover
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


def test_discovers_gradle_repository(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text("plugins { java }")
    assert discover(tmp_path).build_system is BuildSystem.GRADLE


def test_rejects_missing_repository(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        discover(tmp_path / "missing")


def test_ignores_refinery_state_when_checking_worktree(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".refinery").mkdir()
    (tmp_path / ".refinery" / "knowledge.db").touch()

    assert not discover(tmp_path).dirty
