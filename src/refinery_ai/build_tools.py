"""Build-tool command selection that does not mutate target repositories."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


def build_directory(build_files: Iterable[Path]) -> Path:
    """Select the nearest authoritative build root represented by discovery."""

    files = list(build_files)
    if not files:
        return Path(".")
    build_file = min(files, key=lambda path: (len(path.parts), path.as_posix()))
    return build_file.parent


def maven_command_prefix(repository: Path) -> list[str]:
    """Use a checked-in Maven wrapper even when its execute bit is missing."""

    wrapper = repository / "mvnw"
    if not wrapper.is_file():
        return ["mvn"]
    # Older wrapper scripts launch MavenWrapperMain from a checked-in JAR. Some
    # repositories commit the script but omit that (often gitignored) JAR; in
    # that case the system Maven installation is the only usable build tool.
    wrapper_text = wrapper.read_text(errors="replace")
    wrapper_jar = repository / ".mvn/wrapper/maven-wrapper.jar"
    if "org.apache.maven.wrapper.MavenWrapperMain" in wrapper_text and not wrapper_jar.is_file():
        return ["mvn"]
    if os.access(wrapper, os.X_OK):
        return ["./mvnw"]
    return ["sh", "./mvnw"]
