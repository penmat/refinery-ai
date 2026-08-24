"""Infrastructure context providers for databases, Kafka, and Redis."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue

from refinery_ai.context_graph import ContextFragment, ContextRequest
from refinery_ai.models import ContextEdge, ContextEvidence, ContextNode, DiscoverySnapshot

_IGNORED = {".git", ".gradle", ".idea", ".refinery", "build", "target", "graphify-out"}
_JAVA_TYPE = re.compile(r"\b(?:class|interface|record)\s+(\w+)")
_PACKAGE = re.compile(r"\bpackage\s+([\w.]+)\s*;")
_STRING = r'["\']([^"\']+)["\']'


def _dependencies(discovery: DiscoverySnapshot | None) -> str:
    if discovery is None:
        return ""
    return " ".join(
        f"{dependency.group}:{dependency.artifact}" for dependency in discovery.dependencies
    ).casefold()


def _files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        found.update(root.rglob(pattern))
    return sorted(
        path
        for path in found
        if path.is_file() and not _IGNORED.intersection(path.relative_to(root).parts)
    )


def _evidence(
    root: Path, path: Path, text: str, position: int = 0, provider: str = ""
) -> ContextEvidence:
    return ContextEvidence(
        provider=provider,
        file=path.relative_to(root),
        line=text.count("\n", 0, position) + 1,
    )


def _owner(text: str) -> str | None:
    type_match = _JAVA_TYPE.search(text)
    if type_match is None:
        return None
    package_match = _PACKAGE.search(text)
    package = package_match.group(1) if package_match else ""
    name = type_match.group(1)
    return f"java:type:{package}.{name}" if package else f"java:type:{name}"


def _resource(
    fragment: ContextFragment,
    *,
    node_id: str,
    kind: str,
    name: str,
    evidence: ContextEvidence,
    attributes: Mapping[str, JsonValue] | None = None,
) -> None:
    fragment.nodes.append(
        ContextNode(
            id=node_id,
            kind=kind,
            name=name,
            attributes=dict(attributes or {}),
            sources=[evidence],
        )
    )


class DatabaseProvider:
    """Collect database engines, schemas, migrations, and Java data access."""

    name = "database"
    _DEPENDENCIES = (
        "jdbc",
        "jpa",
        "hibernate",
        "postgresql",
        "mysql",
        "mariadb",
        "oracle",
        "h2",
        "hsqldb",
        "flyway",
        "liquibase",
    )

    def supports(self, discovery: DiscoverySnapshot | None) -> bool:
        dependencies = _dependencies(discovery)
        return discovery is None or any(value in dependencies for value in self._DEPENDENCIES)

    def collect(self, request: ContextRequest) -> ContextFragment:
        fragment = ContextFragment(provider=self.name)
        self._collect_dependencies(request, fragment)
        self._collect_configuration(request.repository, fragment)
        self._collect_sql(request.repository, fragment)
        self._collect_java(request.repository, fragment)
        return fragment

    def _collect_dependencies(self, request: ContextRequest, fragment: ContextFragment) -> None:
        if request.discovery is None:
            return
        engine_names = {
            "postgresql": "postgresql",
            "mysql": "mysql",
            "mariadb": "mariadb",
            "h2": "h2",
            "hsqldb": "hsqldb",
            "oracle": "oracle",
        }
        for dependency in request.discovery.dependencies:
            coordinate = f"{dependency.group}:{dependency.artifact}".casefold()
            engine = next(
                (name for token, name in engine_names.items() if token in coordinate), None
            )
            if engine:
                evidence = ContextEvidence(provider=self.name)
                _resource(
                    fragment,
                    node_id=f"database:engine:{engine}",
                    kind="database.engine",
                    name=engine,
                    evidence=evidence,
                    attributes={"dependency": coordinate},
                )

    def _collect_configuration(self, root: Path, fragment: ContextFragment) -> None:
        patterns = ("application*.properties", "application*.yml", "application*.yaml")
        jdbc = re.compile(r"jdbc:(?P<engine>[\w]+):(?P<location>[^\s\"']+)")
        for path in _files(root, patterns):
            text = path.read_text(errors="replace")
            for match in jdbc.finditer(text):
                engine = match.group("engine").casefold()
                evidence = _evidence(root, path, text, match.start(), self.name)
                _resource(
                    fragment,
                    node_id=f"database:connection:{engine}",
                    kind="database.connection",
                    name=engine,
                    evidence=evidence,
                    attributes={"url": f"jdbc:{engine}:<redacted>"},
                )
                fragment.edges.append(
                    ContextEdge(
                        source=f"database:connection:{engine}",
                        target=f"database:engine:{engine}",
                        kind="uses_engine",
                        sources=[evidence],
                    )
                )

    def _collect_sql(self, root: Path, fragment: ContextFragment) -> None:
        create_table = re.compile(
            r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([\w.]+)", re.IGNORECASE
        )
        references = re.compile(r"\bREFERENCES\s+[\"`]?([\w.]+)", re.IGNORECASE)
        for path in _files(root, ("*.sql",)):
            text = path.read_text(errors="replace")
            tables = list(create_table.finditer(text))
            for index, match in enumerate(tables):
                table = match.group(1).casefold()
                evidence = _evidence(root, path, text, match.start(), self.name)
                _resource(
                    fragment,
                    node_id=f"database:table:{table}",
                    kind="database.table",
                    name=table,
                    evidence=evidence,
                    attributes={"defined_in": str(path.relative_to(root))},
                )
                end = tables[index + 1].start() if index + 1 < len(tables) else len(text)
                for reference in references.finditer(text, match.end(), end):
                    target = reference.group(1).casefold()
                    fragment.edges.append(
                        ContextEdge(
                            source=f"database:table:{table}",
                            target=f"database:table:{target}",
                            kind="references",
                            sources=[_evidence(root, path, text, reference.start(), self.name)],
                        )
                    )

    def _collect_java(self, root: Path, fragment: ContextFragment) -> None:
        sql_usage = re.compile(
            rf"(?:query|queryForObject|update|execute)\s*\(\s*{_STRING}", re.IGNORECASE
        )
        tables = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+[\"`]?([\w.]+)", re.IGNORECASE)
        for path in _files(root, ("*.java",)):
            text = path.read_text(errors="replace")
            owner = _owner(text)
            if owner is None:
                continue
            for usage in sql_usage.finditer(text):
                statement = usage.group(1)
                for table_match in tables.finditer(statement):
                    table = table_match.group(1).casefold()
                    operation = statement.lstrip().split(maxsplit=1)[0].upper()
                    edge_kind = "reads" if operation == "SELECT" else "writes"
                    evidence = _evidence(root, path, text, usage.start(), self.name)
                    _resource(
                        fragment,
                        node_id=f"database:table:{table}",
                        kind="database.table",
                        name=table,
                        evidence=evidence,
                    )
                    fragment.edges.append(
                        ContextEdge(
                            source=owner,
                            target=f"database:table:{table}",
                            kind=edge_kind,
                            sources=[evidence],
                        )
                    )


class KafkaProvider:
    """Collect Kafka clusters, topics, producers, and consumers."""

    name = "kafka"

    def supports(self, discovery: DiscoverySnapshot | None) -> bool:
        return discovery is None or "kafka" in _dependencies(discovery)

    def collect(self, request: ContextRequest) -> ContextFragment:
        fragment = ContextFragment(provider=self.name)
        self._collect_configuration(request.repository, fragment)
        self._collect_java(request.repository, fragment)
        return fragment

    def _collect_configuration(self, root: Path, fragment: ContextFragment) -> None:
        bootstrap = re.compile(r"(?:bootstrap-servers|bootstrap\.servers)\s*[:=]\s*([^\s#]+)")
        topic_property = re.compile(r"(?im)^([\w.-]*topic[\w.-]*)\s*[:=]\s*([^\s#]+)")
        for path in _files(
            root, ("application*.properties", "application*.yml", "application*.yaml")
        ):
            text = path.read_text(errors="replace")
            for match in bootstrap.finditer(text):
                evidence = _evidence(root, path, text, match.start(), self.name)
                _resource(
                    fragment,
                    node_id="kafka:cluster:configured",
                    kind="kafka.cluster",
                    name="configured Kafka cluster",
                    evidence=evidence,
                    attributes={"bootstrap_servers": "<redacted>"},
                )
            for match in topic_property.finditer(text):
                topic = match.group(2).strip("\"'")
                evidence = _evidence(root, path, text, match.start(), self.name)
                self._topic(fragment, topic, evidence, attributes={"property": match.group(1)})

    def _collect_java(self, root: Path, fragment: ContextFragment) -> None:
        listener = re.compile(rf"@KafkaListener\s*\([^)]*(?:topics\s*=\s*)?{_STRING}")
        send = re.compile(rf"\.send\s*\(\s*{_STRING}")
        for path in _files(root, ("*.java",)):
            text = path.read_text(errors="replace")
            owner = _owner(text)
            if owner is None:
                continue
            for pattern, edge_kind in ((listener, "consumes"), (send, "publishes")):
                for match in pattern.finditer(text):
                    topic = match.group(1)
                    if topic.startswith("${"):
                        continue
                    evidence = _evidence(root, path, text, match.start(), self.name)
                    self._topic(fragment, topic, evidence)
                    fragment.edges.append(
                        ContextEdge(
                            source=owner,
                            target=f"kafka:topic:{topic}",
                            kind=edge_kind,
                            sources=[evidence],
                        )
                    )

    @staticmethod
    def _topic(
        fragment: ContextFragment,
        topic: str,
        evidence: ContextEvidence,
        *,
        attributes: dict[str, str] | None = None,
    ) -> None:
        _resource(
            fragment,
            node_id=f"kafka:topic:{topic}",
            kind="kafka.topic",
            name=topic,
            evidence=evidence,
            attributes=attributes,
        )


class RedisProvider:
    """Collect Redis connections, keyspaces, and template operations."""

    name = "redis"

    def supports(self, discovery: DiscoverySnapshot | None) -> bool:
        dependencies = _dependencies(discovery)
        return discovery is None or "redis" in dependencies or "redisson" in dependencies

    def collect(self, request: ContextRequest) -> ContextFragment:
        fragment = ContextFragment(provider=self.name)
        self._collect_configuration(request.repository, fragment)
        self._collect_java(request.repository, fragment)
        return fragment

    def _collect_configuration(self, root: Path, fragment: ContextFragment) -> None:
        host = re.compile(
            r"(?:spring\.(?:data\.)?redis\.host|redis:\s*\n\s*host)\s*[:=]\s*([^\s#]+)"
        )
        for path in _files(
            root, ("application*.properties", "application*.yml", "application*.yaml")
        ):
            text = path.read_text(errors="replace")
            for match in host.finditer(text):
                evidence = _evidence(root, path, text, match.start(), self.name)
                _resource(
                    fragment,
                    node_id="redis:instance:configured",
                    kind="redis.instance",
                    name="configured Redis instance",
                    evidence=evidence,
                    attributes={"host": "<redacted>"},
                )

    def _collect_java(self, root: Path, fragment: ContextFragment) -> None:
        keyspace = re.compile(rf"@RedisHash\s*\(\s*{_STRING}")
        operation = re.compile(rf"\.(?:get|set|delete|hasKey)\s*\(\s*{_STRING}")
        for path in _files(root, ("*.java",)):
            text = path.read_text(errors="replace")
            owner = _owner(text)
            if owner is None:
                continue
            for match in keyspace.finditer(text):
                self._redis_edge(
                    root, path, text, match, owner, match.group(1), "maps_to", fragment
                )
            if "RedisTemplate" not in text and "StringRedisTemplate" not in text:
                continue
            for match in operation.finditer(text):
                self._redis_edge(
                    root, path, text, match, owner, match.group(1), "accesses", fragment
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
        evidence = _evidence(root, path, text, match.start(), self.name)
        keyspace_id = f"redis:keyspace:{keyspace}"
        _resource(
            fragment,
            node_id=keyspace_id,
            kind="redis.keyspace",
            name=keyspace,
            evidence=evidence,
        )
        fragment.edges.append(
            ContextEdge(source=owner, target=keyspace_id, kind=edge_kind, sources=[evidence])
        )
