"""Bounded lifecycle for an ephemeral, loopback-only SonarQube container."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from refinery_ai.inspectors.sonarqube.models import (
    SonarQubeError,
    SonarQubeTimeout,
    SonarQubeUnavailable,
    SonarServer,
)
from refinery_ai.logging import get_logger, log_command

logger = get_logger(__name__)

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DockerOptions:
    image: str
    startup_timeout_seconds: int
    poll_interval_seconds: float
    image_pull_timeout_seconds: int = 1800
    keep: bool = False
    output_limit: int = 20_000


class SonarQubeContainer:
    def __init__(
        self,
        assessment_id: str,
        options: DockerOptions,
        *,
        run: RunCommand = subprocess.run,
    ) -> None:
        self.options = options
        self.run = run
        self.name = f"refinery-sonarqube-{assessment_id[:12]}"
        self.server: SonarServer | None = None

    def __enter__(self) -> SonarServer:
        self._require_docker()
        self._ensure_image()
        started = False
        try:
            result = self._run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    self.name,
                    "--publish",
                    "127.0.0.1::9000",
                    "--env",
                    "SONAR_ES_BOOTSTRAP_CHECKS_DISABLE=true",
                    self.options.image,
                ],
                timeout=self.options.startup_timeout_seconds,
            )
            if result.returncode != 0:
                raise SonarQubeError(self._message("Unable to start SonarQube", result))
            started = True
            port = self._published_port()
            digest = self._image_digest()
            self.server = SonarServer(
                self.name, f"http://127.0.0.1:{port}", self.options.image, digest
            )
            return self.server
        except Exception:
            if started and not self.options.keep:
                self.cleanup()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self.options.keep:
            self.cleanup()

    def cleanup(self) -> None:
        self._run(["docker", "rm", "--force", "--volumes", self.name], timeout=30)

    def logs(self) -> str:
        result = self._run(["docker", "logs", "--tail", "100", self.name], timeout=15)
        return (result.stderr or result.stdout)[-self.options.output_limit :]

    def running(self) -> bool:
        result = self._run(
            ["docker", "inspect", "--format", "{{.State.Running}}", self.name], timeout=10
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _require_docker(self) -> None:
        try:
            result = self._run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
        except FileNotFoundError as error:
            raise SonarQubeUnavailable("Docker is not installed") from error
        if result.returncode != 0:
            raise SonarQubeUnavailable(self._message("Docker daemon is unavailable", result))

    def _ensure_image(self) -> None:
        inspect = self._run(["docker", "image", "inspect", self.options.image], timeout=30)
        if inspect.returncode == 0:
            return
        try:
            pull = self._run(
                ["docker", "pull", self.options.image],
                timeout=self.options.image_pull_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise SonarQubeTimeout("SonarQube image pull timed out") from error
        if pull.returncode != 0:
            raise SonarQubeError(self._message("Unable to pull SonarQube image", pull))

    def _published_port(self) -> int:
        deadline = time.monotonic() + min(30, self.options.startup_timeout_seconds)
        while time.monotonic() < deadline:
            result = self._run(["docker", "port", self.name, "9000/tcp"], timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                value = result.stdout.strip().rsplit(":", 1)[-1]
                try:
                    return int(value)
                except ValueError:
                    pass
            time.sleep(min(self.options.poll_interval_seconds, 1))
        raise SonarQubeTimeout("Docker did not publish the SonarQube port")

    def _image_digest(self) -> str | None:
        result = self._run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{index .RepoDigests 0}}",
                self.options.image,
            ],
            timeout=15,
        )
        return result.stdout.strip() or None if result.returncode == 0 else None

    def _run(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        log_command(logger, command)
        try:
            return self.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise SonarQubeTimeout(f"Command timed out: {' '.join(command[:2])}") from error

    def _message(self, prefix: str, result: subprocess.CompletedProcess[str]) -> str:
        detail = (result.stderr or result.stdout).strip()[-self.options.output_limit :]
        return f"{prefix}: {detail}" if detail else prefix
