"""Bounded repository metadata with provenance, never guessed package versions."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

from refinery_ai.modernization.models import Contract, exact_version


class VersionCandidate(Contract):
    version: str
    source: str
    retrieved_at: str
    cached: bool = False


def fetch_metadata(url: str) -> str:
    with urlopen(url, timeout=15) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("Repository metadata exceeds size limit")
    return str(body.decode("utf-8"))


class VersionCandidateProvider:
    def __init__(
        self,
        cache: Path,
        repositories: tuple[str, ...] = ("https://repo.maven.apache.org/maven2",),
        *,
        fetch: Callable[[str], str] = fetch_metadata,
    ) -> None:
        self.cache, self.repositories, self.fetch = cache, repositories, fetch

    def candidates(self, group: str, artifact: str) -> list[VersionCandidate]:
        if not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", v) and ".." not in v for v in (group, artifact)
        ):
            raise ValueError("Invalid Maven coordinates")
        result: dict[str, VersionCandidate] = {}
        for repository in self.repositories:
            parts = urlsplit(repository)
            if parts.scheme != "https" or parts.username or parts.password or parts.query:
                raise ValueError("Repository must use HTTPS without embedded credentials")
            url = (
                f"{repository.rstrip('/')}/{group.replace('.', '/')}/{artifact}/maven-metadata.xml"
            )
            path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
            try:
                body = self.fetch(url)
                if len(body) > 2_000_000:
                    raise ValueError("Repository metadata exceeds size limit")
                root = ET.fromstring(body)
                versions = []
                for node in root.findall("./versioning/versions/version"):
                    try:
                        versions.append(exact_version(node.text or ""))
                    except ValueError:
                        continue
                payload: dict[str, Any] = {
                    "versions": versions,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                }
                self.cache.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload))
                cached = False
            except (OSError, ValueError, ET.ParseError):
                if not path.exists():
                    continue
                payload = json.loads(path.read_text())
                cached = True
            for version in payload["versions"]:
                result[version] = VersionCandidate(
                    version=version, source=url, retrieved_at=payload["retrieved_at"], cached=cached
                )
        return list(result.values())

    def managed_versions(
        self,
        group: str,
        artifact: str,
        version: str,
        *,
        depth: int = 0,
        include_direct: bool = False,
    ) -> dict[tuple[str, str], str]:
        """Resolve concrete managed versions, including bounded imported BOMs."""
        from refinery_ai.modernization.owners import pom_owners

        exact_version(version)
        if depth > 3 or not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", v) and ".." not in v for v in (group, artifact)
        ):
            return {}
        for repository in self.repositories:
            parts = urlsplit(repository)
            if parts.scheme != "https" or parts.username or parts.password or parts.query:
                raise ValueError("Repository must use HTTPS without embedded credentials")
            url = (
                f"{repository.rstrip('/')}/{group.replace('.', '/')}/{artifact}/"
                f"{version}/{artifact}-{version}.pom"
            )
            try:
                content = self.fetch(url)
                if len(content) > 2_000_000:
                    raise ValueError("POM exceeds size limit")
                owners = pom_owners(content)
            except (OSError, ValueError, ET.ParseError):
                continue
            managed: dict[tuple[str, str], str] = {}
            for key, owner in owners.items():
                resolved = owner.get("version")
                if not resolved:
                    continue
                if owner["kind"] == "bom":
                    managed.update(self.managed_versions(*key, resolved, depth=depth + 1))
                elif owner["kind"] == "managed" or (include_direct and owner["kind"] == "direct"):
                    managed[key] = resolved
            return managed
        return {}
