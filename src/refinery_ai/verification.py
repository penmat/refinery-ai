"""Bounded, shell-free build verification."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from refinery_ai.logging import get_logger, log_command
from refinery_ai.models import BuildSystem, DiscoverySnapshot, VerificationResult

logger = get_logger(__name__)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _clean_output(value: str, limit: int) -> str:
    return ANSI_ESCAPE.sub("", value)[-limit:]


def verification_command(snapshot: DiscoverySnapshot) -> list[str] | None:
    root = snapshot.repository
    if snapshot.build_system is BuildSystem.MAVEN:
        return ["./mvnw", "test"] if (root / "mvnw").exists() else ["mvn", "test"]
    if snapshot.build_system is BuildSystem.GRADLE:
        return ["./gradlew", "test"] if (root / "gradlew").exists() else ["gradle", "test"]
    return None


def verify(
    repository: Path,
    command: list[str],
    *,
    timeout: int = 900,
    output_limit: int = 20_000,
) -> VerificationResult:
    """Run an approved argument vector, capturing bounded diagnostic output."""

    started = time.monotonic()
    log_command(logger, command, cwd=repository)
    try:
        result = subprocess.run(
            command, cwd=repository, text=True, capture_output=True, check=False, timeout=timeout
        )
        return VerificationResult(
            command=command,
            exit_code=result.returncode,
            stdout=_clean_output(result.stdout, output_limit),
            stderr=_clean_output(result.stderr, output_limit),
            duration_seconds=time.monotonic() - started,
        )
    except FileNotFoundError as error:
        return VerificationResult(
            command=command,
            exit_code=127,
            stderr=str(error),
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as error:
        return VerificationResult(
            command=command,
            exit_code=124,
            stdout=_clean_output(str(error.stdout or ""), output_limit),
            stderr=f"Timed out after {timeout} seconds",
            duration_seconds=time.monotonic() - started,
        )
