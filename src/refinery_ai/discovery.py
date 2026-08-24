"""Deterministic repository discovery."""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from refinery_ai.models import BuildSystem, Dependency, DiscoverySnapshot, Module

IGNORED_DIRECTORIES = {".git", ".gradle", ".idea", ".refinery", "build", "target"}


def _git(root: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _is_source_file(path: Path) -> bool:
    return not IGNORED_DIRECTORIES.intersection(path.parts)


def _xml_text(element: ET.Element, prefix: str, name: str) -> str | None:
    node = element.find(f"{prefix}{name}")
    return node.text.strip() if node is not None and node.text else None


def _maven_details(path: Path) -> tuple[list[Dependency], str | None]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return [], None
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    prefix = f"{{{namespace}}}" if namespace else ""
    properties = root.find(f"{prefix}properties")
    property_nodes: Iterable[ET.Element] = properties if properties is not None else []
    values = {
        child.tag.rsplit("}", 1)[-1]: child.text.strip() for child in property_nodes if child.text
    }
    java_version = (
        values.get("maven.compiler.release")
        or values.get("maven.compiler.target")
        or values.get("java.version")
    )
    dependencies: list[Dependency] = []
    for item in root.findall(f".//{prefix}dependencies/{prefix}dependency"):
        group = _xml_text(item, prefix, "groupId")
        artifact = _xml_text(item, prefix, "artifactId")
        if group and artifact:
            dependencies.append(
                Dependency(
                    group=group,
                    artifact=artifact,
                    version=_xml_text(item, prefix, "version"),
                    scope=_xml_text(item, prefix, "scope"),
                )
            )
    return dependencies, java_version


def discover(repository: Path) -> DiscoverySnapshot:
    """Inspect build metadata without modifying or building the repository."""

    root = repository.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Repository does not exist: {root}")

    pom_files = [path for path in sorted(root.glob("**/pom.xml")) if _is_source_file(path)]
    gradle_files = [
        path
        for path in sorted([*root.glob("**/build.gradle"), *root.glob("**/build.gradle.kts")])
        if _is_source_file(path)
    ]
    build_system = (
        BuildSystem.MAVEN
        if pom_files
        else BuildSystem.GRADLE
        if gradle_files
        else BuildSystem.UNKNOWN
    )
    build_files = pom_files or gradle_files
    modules: list[Module] = []
    dependencies: list[Dependency] = []
    java_version: str | None = None
    for build_file in build_files:
        module_root = build_file.parent
        main = list((module_root / "src/main/java").rglob("*.java"))
        tests = list((module_root / "src/test/java").rglob("*.java"))
        relative = module_root.relative_to(root)
        modules.append(
            Module(
                name=module_root.name if relative != Path(".") else root.name,
                path=relative,
                java_files=len(main),
                test_files=len(tests),
            )
        )
        if build_file.name == "pom.xml":
            found, version = _maven_details(build_file)
            dependencies.extend(found)
            java_version = java_version or version

    coordinates = " ".join(f"{item.group}:{item.artifact}" for item in dependencies)
    frameworks: list[str] = []
    if "org.springframework" in coordinates:
        frameworks.append("Spring")
    if re.search(r"(^|\s)org\.quarkus:", coordinates):
        frameworks.append("Quarkus")

    # Refinery's own persisted reports must not make an otherwise clean target
    # repository fail the dirty-worktree safety gate on subsequent runs.
    status = _git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).refinery/**",
        ":(exclude)graphify-out/**",
    )
    return DiscoverySnapshot(
        repository=root,
        build_system=build_system,
        build_files=[path.relative_to(root) for path in build_files],
        modules=modules,
        dependencies=dependencies,
        java_version=java_version,
        frameworks=frameworks,
        git_branch=_git(root, "branch", "--show-current"),
        dirty=bool(status),
    )
