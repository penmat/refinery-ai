"""Build-tool command selection that does not mutate target repositories."""

from __future__ import annotations

import os
from pathlib import Path


def maven_command_prefix(repository: Path) -> list[str]:
    """Use a checked-in Maven wrapper even when its execute bit is missing."""

    wrapper = repository / "mvnw"
    if not wrapper.is_file():
        return ["mvn"]
    if os.access(wrapper, os.X_OK):
        return ["./mvnw"]
    return ["sh", "./mvnw"]
