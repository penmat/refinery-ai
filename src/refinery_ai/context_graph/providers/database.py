"""Database context-graph provider."""

from __future__ import annotations

import re
from pathlib import Path

from refinery_ai.context_graph.contracts import ContextFragment, ContextRequest
from refinery_ai.context_graph.models import ContextEdge, ContextEvidence
from refinery_ai.context_graph.providers.common import (
    STRING_LITERAL,
    dependencies,
    evidence,
    files,
    owner,
    resource,
)
from refinery_ai.models import DiscoverySnapshot


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
        found = dependencies(discovery)
        return discovery is None or any(value in found for value in self._DEPENDENCIES)

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
                resource(
                    fragment,
                    node_id=f"database:engine:{engine}",
                    kind="database.engine",
                    name=engine,
                    evidence=ContextEvidence(provider=self.name),
                    attributes={"dependency": coordinate},
                )

    def _collect_configuration(self, root: Path, fragment: ContextFragment) -> None:
        patterns = ("application*.properties", "application*.yml", "application*.yaml")
        jdbc = re.compile(r"jdbc:(?P<engine>[\w]+):(?P<location>[^\s\"']+)")
        for path in files(root, patterns):
            text = path.read_text(errors="replace")
            for match in jdbc.finditer(text):
                engine = match.group("engine").casefold()
                source = evidence(root, path, text, match.start(), self.name)
                resource(
                    fragment,
                    node_id=f"database:connection:{engine}",
                    kind="database.connection",
                    name=engine,
                    evidence=source,
                    attributes={"url": f"jdbc:{engine}:<redacted>"},
                )
                fragment.edges.append(
                    ContextEdge(
                        source=f"database:connection:{engine}",
                        target=f"database:engine:{engine}",
                        kind="uses_engine",
                        sources=[source],
                    )
                )

    def _collect_sql(self, root: Path, fragment: ContextFragment) -> None:
        create_table = re.compile(
            r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([\w.]+)", re.IGNORECASE
        )
        references = re.compile(r"\bREFERENCES\s+[\"`]?([\w.]+)", re.IGNORECASE)
        for path in files(root, ("*.sql",)):
            text = path.read_text(errors="replace")
            tables = list(create_table.finditer(text))
            for index, match in enumerate(tables):
                table = match.group(1).casefold()
                source = evidence(root, path, text, match.start(), self.name)
                resource(
                    fragment,
                    node_id=f"database:table:{table}",
                    kind="database.table",
                    name=table,
                    evidence=source,
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
                            sources=[evidence(root, path, text, reference.start(), self.name)],
                        )
                    )

    def _collect_java(self, root: Path, fragment: ContextFragment) -> None:
        sql_usage = re.compile(
            rf"(?:query|queryForObject|update|execute)\s*\(\s*{STRING_LITERAL}", re.IGNORECASE
        )
        tables = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+[\"`]?([\w.]+)", re.IGNORECASE)
        for path in files(root, ("*.java",)):
            text = path.read_text(errors="replace")
            java_owner = owner(text)
            if java_owner is None:
                continue
            for usage in sql_usage.finditer(text):
                statement = usage.group(1)
                for table_match in tables.finditer(statement):
                    table = table_match.group(1).casefold()
                    operation = statement.lstrip().split(maxsplit=1)[0].upper()
                    edge_kind = "reads" if operation == "SELECT" else "writes"
                    source = evidence(root, path, text, usage.start(), self.name)
                    resource(
                        fragment,
                        node_id=f"database:table:{table}",
                        kind="database.table",
                        name=table,
                        evidence=source,
                    )
                    fragment.edges.append(
                        ContextEdge(
                            source=java_owner,
                            target=f"database:table:{table}",
                            kind=edge_kind,
                            sources=[source],
                        )
                    )
