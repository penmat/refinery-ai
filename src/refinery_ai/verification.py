"""Bounded, shell-free build verification."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from refinery_ai.build_tools import maven_command_prefix
from refinery_ai.logging import get_logger, log_command
from refinery_ai.models import BuildSystem, DiscoverySnapshot, VerificationResult

logger = get_logger(__name__)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MACOS_JAVA_HOME_PROBE_WARNING = (
    "The operation couldn\u2019t be completed. Unable to locate a Java Runtime.\n"
    "Please visit http://www.java.com for information on installing Java.\n"
)


def _clean_output(value: str, limit: int) -> str:
    return ANSI_ESCAPE.sub("", value)[-limit:]


def _clean_stderr(value: str, exit_code: int, limit: int) -> str:
    """Remove a misleading macOS java_home probe warning after a successful command."""

    cleaned = ANSI_ESCAPE.sub("", value)
    if exit_code == 0:
        cleaned = cleaned.replace(MACOS_JAVA_HOME_PROBE_WARNING, "")
    return cleaned[-limit:]


def verification_command(snapshot: DiscoverySnapshot) -> list[str] | None:
    root = snapshot.repository
    if snapshot.build_system is BuildSystem.MAVEN:
        return [*maven_command_prefix(root), "clean", "test"]
    if snapshot.build_system is BuildSystem.GRADLE:
        return ["./gradlew", "test"] if (root / "gradlew").exists() else ["gradle", "test"]
    return None


def verify(
    repository: Path,
    command: list[str],
    *,
    timeout: int = 900,
    output_limit: int = 20_000,
    environment: dict[str, str] | None = None,
) -> VerificationResult:
    """Run an approved argument vector, capturing bounded diagnostic output."""

    started = time.monotonic()
    log_command(logger, command, cwd=repository)
    try:
        result = subprocess.run(
            command,
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=environment,
        )
        verification = VerificationResult(
            command=command,
            exit_code=result.returncode,
            stdout=_clean_output(result.stdout, output_limit),
            stderr=_clean_stderr(result.stderr, result.returncode, output_limit),
            duration_seconds=time.monotonic() - started,
        )
        logger.debug(
            "Command completed with exit code %s in %.2fs\nstdout:\n%s\nstderr:\n%s",
            verification.exit_code,
            verification.duration_seconds,
            verification.stdout,
            verification.stderr,
        )
        return verification
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
