"""Redis context-graph provider."""

from __future__ import annotations

import re
from pathlib import Path

from refinery_ai.context_graph.contracts import ContextFragment, ContextRequest
from refinery_ai.context_graph.models import ContextEdge
from refinery_ai.context_graph.providers.common import (
    STRING_LITERAL,
    dependencies,
    evidence,
    files,
    owner,
    resource,
)
from refinery_ai.models import DiscoverySnapshot


class RedisProvider:
    """Collect Redis connections, keyspaces, and template operations."""

    name = "redis"

    def supports(self, discovery: DiscoverySnapshot | None) -> bool:
        found = dependencies(discovery)
        return discovery is None or "redis" in found or "redisson" in found

    def collect(self, request: ContextRequest) -> ContextFragment:
        fragment = ContextFragment(provider=self.name)
        self._collect_configuration(request.repository, fragment)
        self._collect_java(request.repository, fragment)
        return fragment

    def _collect_configuration(self, root: Path, fragment: ContextFragment) -> None:
        host = re.compile(
            r"(?:spring\.(?:data\.)?redis\.host|redis:\s*\n\s*host)\s*[:=]\s*([^\s#]+)"
        )
        for path in files(
            root, ("application*.properties", "application*.yml", "application*.yaml")
        ):
            text = path.read_text(errors="replace")
            for match in host.finditer(text):
                source = evidence(root, path, text, match.start(), self.name)
                resource(
                    fragment,
                    node_id="redis:instance:configured",
                    kind="redis.instance",
                    name="configured Redis instance",
                    evidence=source,
                    attributes={"host": "<redacted>"},
                )

    def _collect_java(self, root: Path, fragment: ContextFragment) -> None:
        keyspace = re.compile(rf"@RedisHash\s*\(\s*{STRING_LITERAL}")
        operation = re.compile(rf"\.(?:get|set|delete|hasKey)\s*\(\s*{STRING_LITERAL}")
        for path in files(root, ("*.java",)):
            text = path.read_text(errors="replace")
            java_owner = owner(text)
            if java_owner is None:
                continue
            for match in keyspace.finditer(text):
                self._redis_edge(
                    root, path, text, match, java_owner, match.group(1), "maps_to", fragment
                )
            if "RedisTemplate" not in text and "StringRedisTemplate" not in text:
                continue
            for match in operation.finditer(text):
                self._redis_edge(
                    root, path, text, match, java_owner, match.group(1), "accesses", fragment
                )

    def _redis_edge(
        self,
        root: Path,
        path: Path,
        text: str,
        match: re.Match[str],
        owner: str,
        keyspace: str,
        edge_kind: str,
        fragment: ContextFragment,
    ) -> None:
        source = evidence(root, path, text, match.start(), self.name)
        keyspace_id = f"redis:keyspace:{keyspace}"
        resource(
            fragment,
            node_id=keyspace_id,
            kind="redis.keyspace",
            name=keyspace,
            evidence=source,
        )
        fragment.edges.append(
            ContextEdge(source=owner, target=keyspace_id, kind=edge_kind, sources=[source])
        )
