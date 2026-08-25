"""Maven Package URL normalization."""

from __future__ import annotations

from urllib.parse import quote

from refinery_ai.inspectors.dependencies.models import ResolvedDependency


def maven_purl(dependency: ResolvedDependency) -> str:
    """Return a canonical, versioned Maven Package URL."""

    namespace = quote(dependency.group, safe="._-")
    name = quote(dependency.artifact, safe="._-")
    version = quote(dependency.version, safe="._-")
    return f"pkg:maven/{namespace}/{name}@{version}"
