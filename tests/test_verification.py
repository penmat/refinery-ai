from pathlib import Path

from refinery_ai.models import BuildSystem, DiscoverySnapshot
from refinery_ai.verification import (
    MACOS_JAVA_HOME_PROBE_WARNING,
    _clean_stderr,
    verification_command,
    verify,
)


def test_maven_verification_cleans_before_testing(tmp_path: Path) -> None:
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)

    assert verification_command(snapshot) == ["mvn", "clean", "test"]


def test_non_executable_maven_wrapper_runs_through_shell(tmp_path: Path) -> None:
    (tmp_path / "mvnw").touch()
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)

    assert verification_command(snapshot) == ["sh", "./mvnw", "clean", "test"]


def test_executable_maven_wrapper_runs_directly(tmp_path: Path) -> None:
    wrapper = tmp_path / "mvnw"
    wrapper.touch(mode=0o755)
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)

    assert verification_command(snapshot) == ["./mvnw", "clean", "test"]


def test_maven_wrapper_missing_required_jar_falls_back_to_maven(tmp_path: Path) -> None:
    (tmp_path / "mvnw").write_text(
        "java org.apache.maven.wrapper.MavenWrapperMain \"$@\"\n"
    )
    snapshot = DiscoverySnapshot(repository=tmp_path, build_system=BuildSystem.MAVEN)

    assert verification_command(snapshot) == ["mvn", "clean", "test"]


def test_nested_maven_project_is_verified_from_its_module_root(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pom.xml").touch()
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system=BuildSystem.MAVEN,
        build_files=[Path("backend/pom.xml")],
    )

    assert verification_command(snapshot) == [
        "mvn",
        "-f",
        "backend/pom.xml",
        "clean",
        "test",
    ]


def test_nested_gradle_project_is_verified_from_its_module_root(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "build.gradle").touch()
    snapshot = DiscoverySnapshot(
        repository=tmp_path,
        build_system=BuildSystem.GRADLE,
        build_files=[Path("backend/build.gradle")],
    )

    assert verification_command(snapshot) == ["gradle", "-p", "backend", "test"]


def test_successful_command_omits_benign_macos_java_home_probe_warning() -> None:
    assert _clean_stderr(MACOS_JAVA_HOME_PROBE_WARNING, 0, 20_000) == ""


def test_failed_command_preserves_macos_java_home_probe_warning() -> None:
    assert _clean_stderr(MACOS_JAVA_HOME_PROBE_WARNING, 1, 20_000) == (
        MACOS_JAVA_HOME_PROBE_WARNING
    )


def test_verification_reports_success(tmp_path: Path) -> None:
    result = verify(tmp_path, ["python3", "-c", "print('green')"])
    assert result.green
    assert "green" in result.stdout


def test_verification_reports_missing_command(tmp_path: Path) -> None:
    result = verify(tmp_path, ["definitely-not-a-real-command"])
    assert result.exit_code == 127


def test_verification_bounds_output(tmp_path: Path) -> None:
    result = verify(tmp_path, ["python3", "-c", "print('x' * 100)"], output_limit=10)
    assert len(result.stdout) == 10


def test_verification_removes_ansi_sequences(tmp_path: Path) -> None:
    result = verify(tmp_path, ["python3", "-c", "print('\\x1b[31merror\\x1b[0m')"])
    assert result.stdout.strip() == "error"
