"""Repository fingerprinting for context-graph freshness checks."""

from __future__ import annotations

import hashlib
from pathlib import Path


def repository_fingerprint(repository: Path) -> str:
    """Hash relevant repository inputs while ignoring generated state."""

    ignored = {
        ".git",
        ".gradle",
        ".idea",
        ".refinery",
        ".codegraph",
        "build",
        "target",
        "graphify-out",
    }
    relevant_names = {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    }
    relevant_suffixes = {".java", ".kt", ".xml", ".sql", ".properties", ".yml", ".yaml"}
    digest = hashlib.sha256()
    for path in sorted(repository.rglob("*")):
        if not path.is_file() or ignored.intersection(path.relative_to(repository).parts):
            continue
        if path.name not in relevant_names and path.suffix.casefold() not in relevant_suffixes:
            continue
        relative = path.relative_to(repository)
        digest.update(relative.as_posix().encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()
