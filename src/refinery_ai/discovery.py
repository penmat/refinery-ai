"""Deterministic repository discovery."""

from __future__ import annotations

import re
import ssl
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast
from urllib.parse import quote
from urllib.request import Request, urlopen

from refinery_ai.logging import get_logger, log_command
from refinery_ai.models import BuildSystem, Dependency, DiscoverySnapshot, Module

logger = get_logger(__name__)

IGNORED_DIRECTORIES = {".git", ".gradle", ".idea", ".refinery", "build", "target"}
MAVEN_CENTRAL_URL = "https://repo.maven.apache.org/maven2"
_COORDINATE = re.compile(r"^[A-Za-z0-9_.+\-]+$")


def _git(root: Path, *args: str) -> str | None:
    command = ["git", *args]
    log_command(logger, command, cwd=root)
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _is_source_file(path: Path) -> bool:
    return not IGNORED_DIRECTORIES.intersection(path.parts)


def non_utf8_application_properties(root: Path) -> list[Path]:
    invalid: list[Path] = []
    for path in sorted(root.glob("**/resources/application*.properties")):
        if not _is_source_file(path):
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            invalid.append(path.relative_to(root))
        except OSError:
            continue
    return invalid


def deferred_datasource_initialization_properties(
    root: Path, dependencies: list[Dependency]
) -> list[Path]:
    """Find JPA modules whose data.sql must wait for Hibernate schema creation."""

    has_jpa = any(
        dependency.artifact in {"spring-boot-starter-data-jpa", "hibernate-core"}
        for dependency in dependencies
    )
    if not has_jpa:
        return []
    targets: list[Path] = []
    for data_sql in sorted(root.glob("**/resources/data.sql")):
        if not _is_source_file(data_sql):
            continue
        resources = data_sql.parent
        configured = False
        for candidate in resources.glob("application*"):
            if candidate.suffix not in {".properties", ".yml", ".yaml"}:
                continue
            try:
                content = candidate.read_text(errors="replace")
            except OSError:
                continue
            if re.search(
                r"spring\.jpa\.defer-datasource-initialization\s*[=:]\s*true",
                content,
                re.IGNORECASE,
            ) or re.search(
                r"defer-datasource-initialization\s*:\s*true", content, re.IGNORECASE
            ):
                configured = True
                break
        if not configured:
            targets.append((resources / "application.properties").relative_to(root))
    return targets


def _xml_text(element: ET.Element, prefix: str, name: str) -> str | None:
    node = element.find(f"{prefix}{name}")
    return node.text.strip() if node is not None and node.text else None


def maven_details(content: str) -> tuple[list[Dependency], str | None]:
    """Parse the Maven fields used by discovery from repository evidence text."""

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], None
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    prefix = f"{{{namespace}}}" if namespace else ""
    properties = root.find(f"{prefix}properties")
    property_nodes: Iterable[ET.Element] = properties if properties is not None else []
    values = {
        child.tag.rsplit("}", 1)[-1]: child.text.strip() for child in property_nodes if child.text
    }
    property_reference = re.compile(r"^\$\{([^}]+)}$")

    def resolved(value: str | None) -> str | None:
        seen: set[str] = set()
        while value and (match := property_reference.fullmatch(value)):
            name = match.group(1)
            if name in seen:
                return None
            seen.add(name)
            value = values.get(name)
        return value

    java_version = (
        resolved(values.get("maven.compiler.release"))
        or resolved(values.get("maven.compiler.target"))
        or resolved(values.get("java.version"))
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


def _parent_coordinates(content: str) -> tuple[str, str, str, str | None] | None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    prefix = f"{{{namespace}}}" if namespace else ""
    parent = root.find(f"{prefix}parent")
    if parent is None:
        return None
    group = _xml_text(parent, prefix, "groupId")
    artifact = _xml_text(parent, prefix, "artifactId")
    version = _xml_text(parent, prefix, "version")
    relative = parent.find(f"{prefix}relativePath")
    relative_path = None if relative is None else (relative.text or "").strip()
    if not group or not artifact or not version:
        return None
    if not all(_COORDINATE.fullmatch(value) for value in (group, artifact, version)):
        return None
    return group, artifact, version, relative_path


def _maven_central_pom(group: str, artifact: str, version: str, timeout: float = 10) -> str:
    group_path = "/".join(quote(part, safe="") for part in group.split("."))
    filename = f"{quote(artifact, safe='')}-{quote(version, safe='')}.pom"
    url = (
        f"{MAVEN_CENTRAL_URL}/{group_path}/{quote(artifact, safe='')}/"
        f"{quote(version, safe='')}/{filename}"
    )
    request = Request(url, headers={"User-Agent": "refinery-ai/0.1"})
    try:
        import certifi
    except ImportError:
        context = ssl.create_default_context()
    else:
        context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout, context=context) as response:
        return cast(bytes, response.read()).decode("utf-8")


def resolve_parent_java_version(
    pom_path: Path,
    content: str,
    *,
    fetch_parent: Callable[[str, str, str], str] = _maven_central_pom,
    max_depth: int = 5,
) -> str | None:
    """Resolve a missing Java level through local or Maven Central parent POMs."""

    current_path = pom_path
    current_content = content
    visited: set[tuple[str, str, str]] = set()
    for _ in range(max_depth):
        _, java_version = maven_details(current_content)
        if java_version:
            return "8" if java_version == "1.8" else java_version
        parent = _parent_coordinates(current_content)
        if parent is None:
            return None
        group, artifact, version, relative_path = parent
        coordinates = (group, artifact, version)
        if coordinates in visited:
            return None
        visited.add(coordinates)

        local_path = None
        if relative_path != "":
            local_path = (current_path.parent / (relative_path or "../pom.xml")).resolve()
        try:
            if local_path is not None and local_path.is_file():
                current_path = local_path
                current_content = local_path.read_text()
                continue
            cached = (
                Path.home()
                / ".m2/repository"
                / Path(*group.split("."))
                / artifact
                / version
                / f"{artifact}-{version}.pom"
            )
            if cached.is_file():
                current_path = cached
                current_content = cached.read_text()
                continue
            current_content = fetch_parent(group, artifact, version)
            current_path = Path(f"{artifact}-{version}.pom")
        except (OSError, UnicodeError):
            return None
    return None


def _maven_details(path: Path) -> tuple[list[Dependency], str | None]:
    try:
        content = path.read_text()
        dependencies, version = maven_details(content)
        return dependencies, version or resolve_parent_java_version(path, content)
    except OSError:
        return [], None


def _spring_boot_parent_version(path: Path) -> str | None:
    try:
        parent = _parent_coordinates(path.read_text())
    except (OSError, UnicodeError):
        return None
    if parent is None:
        return None
    group, artifact, version, _ = parent
    if group == "org.springframework.boot" and artifact == "spring-boot-starter-parent":
        return version
    return None


def select_build_files(
    root: Path,
    pom_files: list[Path],
    gradle_files: list[Path],
    *,
    root_has_gradle_marker: bool = False,
) -> tuple[BuildSystem, list[Path]]:
    """Choose the repository build from root authority, then nearest descriptor depth."""

    def relative(path: Path) -> Path:
        return path.relative_to(root) if path.is_absolute() else path

    root_pom = any(relative(path) == Path("pom.xml") for path in pom_files)
    if root_pom:
        return BuildSystem.MAVEN, pom_files
    if root_has_gradle_marker:
        return BuildSystem.GRADLE, gradle_files
    if not pom_files:
        return (BuildSystem.GRADLE, gradle_files) if gradle_files else (BuildSystem.UNKNOWN, [])
    if not gradle_files:
        return BuildSystem.MAVEN, pom_files
    pom_depth = min(len(relative(path).parts) for path in pom_files)
    gradle_depth = min(len(relative(path).parts) for path in gradle_files)
    return (
        (BuildSystem.GRADLE, gradle_files)
        if gradle_depth < pom_depth
        else (BuildSystem.MAVEN, pom_files)
    )


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
    root_has_gradle_marker = any(
        (root / name).is_file()
        for name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
    )
    build_system, build_files = select_build_files(
        root, pom_files, gradle_files, root_has_gradle_marker=root_has_gradle_marker
    )
    modules: list[Module] = []
    dependencies: list[Dependency] = []
    java_version: str | None = None
    spring_boot_version: str | None = None
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
            spring_boot_version = spring_boot_version or _spring_boot_parent_version(build_file)

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
        ":(exclude).codegraph/**",
        ":(exclude)graphify-out/**",
    )
    return DiscoverySnapshot(
        repository=root,
        build_system=build_system,
        build_files=[path.relative_to(root) for path in build_files],
        modules=modules,
        dependencies=dependencies,
        java_version=java_version,
        spring_boot_version=spring_boot_version,
        non_utf8_application_properties=non_utf8_application_properties(root),
        deferred_datasource_initialization_properties=(
            deferred_datasource_initialization_properties(root, dependencies)
        ),
        frameworks=frameworks,
        git_branch=_git(root, "branch", "--show-current"),
        dirty=bool(status),
    )
