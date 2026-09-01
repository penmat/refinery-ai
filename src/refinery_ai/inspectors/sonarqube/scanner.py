"""Maven and Gradle SonarScanner selection and bounded execution."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from refinery_ai.build_tools import maven_command_prefix
from refinery_ai.inspectors.sonarqube.models import (
    SonarQubeError,
    SonarQubeTimeout,
    SonarQubeUnavailable,
    SonarScannerContext,
)
from refinery_ai.logging import get_logger, log_command
from refinery_ai.models import BuildSystem, DiscoverySnapshot

logger = get_logger(__name__)

RunCommand = Callable[..., subprocess.CompletedProcess[str]]
_TASK_ID = re.compile(r"^ceTaskId=(.+)$", re.MULTILINE)


class ScannerRunner:
    def __init__(
        self,
        maven_scanner_version: str,
        gradle_scanner_version: str,
        *,
        timeout_seconds: int,
        output_limit: int,
        run: RunCommand = subprocess.run,
    ) -> None:
        self.maven_scanner_version = maven_scanner_version
        self.gradle_scanner_version = gradle_scanner_version
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit
        self.run = run

    def scan(self, context: SonarScannerContext, snapshot: DiscoverySnapshot) -> tuple[str, str]:
        with tempfile.TemporaryDirectory(prefix="refinery-sonarqube-") as temporary:
            temporary_root = Path(temporary)
            metadata_path = temporary_root / "report-task.txt"
            gradle_init_script = None
            if snapshot.build_system is BuildSystem.GRADLE and not self._gradle_plugin_configured(
                context.repository
            ):
                gradle_init_script = temporary_root / "sonarqube.init.gradle"
                self._write_gradle_init_script(gradle_init_script)
            command, scanner = self.command(
                context,
                snapshot,
                metadata_path=metadata_path,
                gradle_init_script=gradle_init_script,
            )
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(temporary_root),
                "GRADLE_USER_HOME": str(temporary_root / "gradle"),
                "SONAR_HOST_URL": context.server_url,
                "SONAR_TOKEN": context.token,
            }
            if java_home := os.environ.get("JAVA_HOME"):
                environment["JAVA_HOME"] = java_home
            log_command(logger, command, cwd=context.repository)
            try:
                result = self.run(
                    command,
                    cwd=context.repository,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout_seconds,
                )
            except FileNotFoundError as error:
                raise SonarQubeUnavailable(
                    f"Required build command is unavailable: {command[0]}"
                ) from error
            except subprocess.TimeoutExpired as error:
                raise SonarQubeTimeout("SonarQube scanner timed out") from error
            output = self._redact(
                (result.stdout + "\n" + result.stderr)[-self.output_limit :], context
            )
            if result.returncode != 0:
                raise SonarQubeError(
                    f"SonarQube scanner failed with exit code {result.returncode}: {output}"
                )
            task_id = self._task_id(metadata_path)
            return task_id, scanner

    def command(
        self,
        context: SonarScannerContext,
        snapshot: DiscoverySnapshot,
        *,
        metadata_path: Path | None = None,
        gradle_init_script: Path | None = None,
    ) -> tuple[list[str], str]:
        metadata_argument = (
            [f"-Dsonar.scanner.metadataFilePath={metadata_path}"] if metadata_path else []
        )
        if snapshot.build_system is BuildSystem.MAVEN:
            goal = (
                "org.sonarsource.scanner.maven:sonar-maven-plugin:"
                f"{self.maven_scanner_version}:sonar"
            )
            return [
                *maven_command_prefix(context.repository),
                "compile",
                goal,
                f"-Dsonar.projectKey={context.project_key}",
                *metadata_argument,
            ], f"sonar-maven-plugin:{self.maven_scanner_version}"
        if snapshot.build_system is BuildSystem.GRADLE:
            configured = self._gradle_plugin_configured(context.repository)
            if not configured and gradle_init_script is None:
                raise SonarQubeUnavailable(
                    "Gradle SonarQube plugin is not configured and no temporary init script "
                    "was supplied"
                )
            executable = "./gradlew" if (context.repository / "gradlew").is_file() else "gradle"
            init_arguments = (
                ["--init-script", str(gradle_init_script)] if gradle_init_script else []
            )
            return [
                executable,
                *init_arguments,
                "classes",
                "sonar",
                f"-Dsonar.projectKey={context.project_key}",
                *metadata_argument,
            ], (
                "sonar-gradle-plugin:project-configured"
                if configured
                else f"sonar-gradle-plugin:{self.gradle_scanner_version}:temporary-init"
            )
        raise SonarQubeUnavailable("SonarQube supports only detected Maven or Gradle projects")

    @staticmethod
    def _task_id(path: Path) -> str:
        try:
            content = path.read_text()
        except OSError as error:
            raise SonarQubeError("Scanner completed without report-task.txt") from error
        match = _TASK_ID.search(content)
        if not match:
            raise SonarQubeError("Scanner report does not contain a compute-task ID")
        return match.group(1).strip()

    @staticmethod
    def _gradle_plugin_configured(repository: Path) -> bool:
        for name in ("build.gradle", "build.gradle.kts"):
            for path in repository.rglob(name):
                try:
                    if "org.sonarqube" in path.read_text(errors="replace"):
                        return True
                except OSError:
                    continue
        return False

    def _write_gradle_init_script(self, path: Path) -> None:
        path.write_text(
            "initscript {\n"
            "    repositories { gradlePluginPortal() }\n"
            "    dependencies {\n"
            "        classpath 'org.sonarsource.scanner.gradle:sonarqube-gradle-plugin:"
            f"{self.gradle_scanner_version}'\n"
            "    }\n"
            "}\n"
            "allprojects {\n"
            "    if (project == rootProject) {\n"
            "        apply plugin: org.sonarqube.gradle.SonarQubePlugin\n"
            "    }\n"
            "}\n"
        )

    @staticmethod
    def _redact(value: str, context: SonarScannerContext) -> str:
        return value.replace(context.token, "[REDACTED]").replace(
            context.server_url, "[LOCAL_SONARQUBE]"
        )
