"""Resolve Maven and Gradle dependency graphs without editing the target."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from refinery_ai.inspectors.dependencies.models import (
    DependencyAnalysisError,
    DependencyAnalysisTimeout,
    ResolvedDependency,
)
from refinery_ai.models import BuildSystem

_GRADLE_INIT = r"""import groovy.json.JsonOutput
import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.gradle.api.artifacts.result.ResolvedDependencyResult

gradle.projectsEvaluated {
    def root = gradle.rootProject
    root.tasks.register("_refineryResolvedDependencies") {
        doLast {
            def dependencies = []
            root.allprojects.each { project ->
                def candidates = project.configurations.findAll {
                    it.canBeResolved && it.name == "runtimeClasspath"
                }
                if (candidates.isEmpty()) {
                    candidates = project.configurations.findAll {
                        it.canBeResolved && it.name == "compileClasspath"
                    }
                }
                candidates.each { configuration ->
                    def rootComponent = configuration.incoming.resolutionResult.rootComponent.get()
                    def directIds = rootComponent.dependencies.findAll {
                        it instanceof ResolvedDependencyResult
                    }.collect { it.selected.id } as Set
                    configuration.incoming.resolutionResult.allComponents.each { component ->
                        if (component.id instanceof ModuleComponentIdentifier) {
                            dependencies << [
                                group: component.id.group,
                                artifact: component.id.module,
                                version: component.id.version,
                                scope: configuration.name,
                                direct: directIds.contains(component.id)
                            ]
                        }
                    }
                }
            }
            def output = new File(System.getProperty("refinery.dependencies.output"))
            output.text = JsonOutput.toJson(dependencies)
        }
    }
}
"""


class ResolvedDependencyCollector:
    def __init__(self, maven_plugin_version: str, timeout_seconds: int, output_limit: int) -> None:
        self.maven_plugin_version = maven_plugin_version
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit

    def collect(self, repository: Path, build_system: BuildSystem) -> list[ResolvedDependency]:
        if build_system is BuildSystem.MAVEN:
            return self._maven(repository)
        if build_system is BuildSystem.GRADLE:
            return self._gradle(repository)
        raise DependencyAnalysisError("No supported Maven or Gradle build was detected")

    def _maven(self, repository: Path) -> list[ResolvedDependency]:
        executable = str(repository / "mvnw") if (repository / "mvnw").is_file() else "mvn"
        with tempfile.TemporaryDirectory(prefix="refinery-dependencies-") as directory:
            output = Path(directory) / "dependencies.json"
            command = [
                executable,
                f"org.apache.maven.plugins:maven-dependency-plugin:{self.maven_plugin_version}:tree",
                "-DoutputType=json",
                f"-DoutputFile={output}",
                "-DappendOutput=true",
                "-Dscope=runtime",
                "--batch-mode",
                "--no-transfer-progress",
            ]
            self._run(command, repository, "Maven")
            if not output.is_file():
                raise DependencyAnalysisError("Maven completed without producing a dependency tree")
            try:
                payload = _json_documents(output.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise DependencyAnalysisError(
                    "Maven produced an unreadable dependency tree"
                ) from error
        dependencies: list[ResolvedDependency] = []
        for tree in payload:
            self._walk_maven(tree, dependencies, depth=0)
        return _unique(dependencies)

    def _walk_maven(
        self, node: object, dependencies: list[ResolvedDependency], *, depth: int
    ) -> None:
        if not isinstance(node, dict):
            return
        group = node.get("groupId")
        artifact = node.get("artifactId")
        version = node.get("version")
        if (
            depth
            and isinstance(group, str)
            and bool(group)
            and isinstance(artifact, str)
            and bool(artifact)
            and isinstance(version, str)
            and bool(version)
        ):
            dependencies.append(
                ResolvedDependency(
                    group=group,
                    artifact=artifact,
                    version=version,
                    scope=_string(node.get("scope")),
                    packaging=_string(node.get("type")),
                    classifier=_string(node.get("classifier")),
                    direct=depth == 1,
                )
            )
        children = node.get("children", [])
        if isinstance(children, list):
            for child in children:
                self._walk_maven(child, dependencies, depth=depth + 1)

    def _gradle(self, repository: Path) -> list[ResolvedDependency]:
        executable = str(repository / "gradlew") if (repository / "gradlew").is_file() else "gradle"
        with tempfile.TemporaryDirectory(prefix="refinery-dependencies-") as directory:
            temporary = Path(directory)
            init_script = temporary / "dependencies.init.gradle"
            output = temporary / "dependencies.json"
            init_script.write_text(_GRADLE_INIT)
            command = [
                executable,
                "--init-script",
                str(init_script),
                f"-Drefinery.dependencies.output={output}",
                "_refineryResolvedDependencies",
                "--quiet",
                "--no-daemon",
            ]
            self._run(command, repository, "Gradle")
            if not output.is_file():
                raise DependencyAnalysisError(
                    "Gradle completed without producing a dependency graph"
                )
            try:
                payload = json.loads(output.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise DependencyAnalysisError(
                    "Gradle produced an unreadable dependency graph"
                ) from error
        if not isinstance(payload, list):
            raise DependencyAnalysisError("Gradle dependency graph has an unexpected format")
        dependencies = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            group, artifact, version = item.get("group"), item.get("artifact"), item.get("version")
            if (
                isinstance(group, str)
                and bool(group)
                and isinstance(artifact, str)
                and bool(artifact)
                and isinstance(version, str)
                and bool(version)
            ):
                dependencies.append(
                    ResolvedDependency(
                        group=group,
                        artifact=artifact,
                        version=version,
                        scope=_string(item.get("scope")),
                        direct=item.get("direct") is True,
                    )
                )
        return _unique(dependencies)

    def _run(self, command: list[str], repository: Path, label: str) -> None:
        try:
            result = subprocess.run(
                command,
                cwd=repository,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise DependencyAnalysisTimeout(
                f"{label} dependency resolution timed out after {self.timeout_seconds} seconds"
            ) from error
        except OSError as error:
            raise DependencyAnalysisError(f"{label} executable is unavailable: {error}") from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[-self.output_limit :]
            raise DependencyAnalysisError(
                f"{label} dependency resolution failed with exit code {result.returncode}: {detail}"
            )


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _unique(dependencies: list[ResolvedDependency]) -> list[ResolvedDependency]:
    by_coordinate: dict[tuple[str, str, str], ResolvedDependency] = {}
    for dependency in dependencies:
        key = dependency.group, dependency.artifact, dependency.version
        previous = by_coordinate.get(key)
        if previous is None or dependency.direct:
            by_coordinate[key] = dependency
    return sorted(by_coordinate.values())


def _json_documents(content: str) -> list[object]:
    """Decode the JSON trees appended by each Maven reactor module."""

    decoder = json.JSONDecoder()
    documents: list[object] = []
    position = 0
    while position < len(content):
        while position < len(content) and content[position].isspace():
            position += 1
        if position == len(content):
            break
        document, position = decoder.raw_decode(content, position)
        documents.append(document)
    return documents
