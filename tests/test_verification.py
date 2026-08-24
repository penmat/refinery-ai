from pathlib import Path

from refinery_ai.verification import verify


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
