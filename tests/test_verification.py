from pathlib import Path

from refinery_ai.verification import MACOS_JAVA_HOME_PROBE_WARNING, _clean_stderr, verify


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
