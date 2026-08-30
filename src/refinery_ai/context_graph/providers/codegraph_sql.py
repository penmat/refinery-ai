"""Read-only export from CodeGraph's SQLite index into Refinery models."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from refinery_ai.context_graph.models import ContextEdge, ContextEvidence, ContextNode

_NODE_COLUMNS = {
    "id",
    "kind",
    "name",
    "qualified_name",
    "file_path",
    "language",
    "start_line",
    "end_line",
    "start_column",
    "end_column",
}
_EDGE_COLUMNS = {"source", "target", "kind", "metadata", "line", "col", "provenance"}


class CodeGraphSchemaError(RuntimeError):
    """Raised when a CodeGraph database cannot be exported safely."""


class CodeGraphSqlExporter:
    """Normalize the stable CodeGraph node and edge tables."""

    def export(self, database: Path) -> tuple[list[ContextNode], list[ContextEdge], int]:
        if not database.is_file():
            raise CodeGraphSchemaError(f"CodeGraph database was not created: {database}")
        try:
            with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                self._validate(connection)
                version = self._schema_version(connection)
                nodes = [self._node(row) for row in connection.execute("SELECT * FROM nodes")]
                node_ids = {node.id for node in nodes}
                node_files = {
                    node.id: node.sources[0].file for node in nodes if node.sources
                }
                edges = [
                    edge
                    for row in connection.execute("SELECT * FROM edges")
                    if (
                        edge := self._edge(
                            row, node_files.get(f"codegraph:{row['source']}")
                        )
                    ).source
                    in node_ids
                    and edge.target in node_ids
                ]
        except sqlite3.Error as error:
            raise CodeGraphSchemaError(f"Unable to read CodeGraph database: {error}") from error
        return nodes, edges, version

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    def _validate(self, connection: sqlite3.Connection) -> None:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing_tables = {"schema_versions", "nodes", "edges"} - tables
        if missing_tables:
            raise CodeGraphSchemaError(
                "Unsupported CodeGraph database; missing tables: "
                + ", ".join(sorted(missing_tables))
            )
        for table, required in (("nodes", _NODE_COLUMNS), ("edges", _EDGE_COLUMNS)):
            missing = required - self._columns(connection, table)
            if missing:
                raise CodeGraphSchemaError(
                    f"Unsupported CodeGraph {table} schema; missing columns: "
                    + ", ".join(sorted(missing))
                )

    @staticmethod
    def _schema_version(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT max(version) FROM schema_versions").fetchone()
        return int(row[0] or 0)

    @staticmethod
    def _node(row: sqlite3.Row) -> ContextNode:
        raw = dict(row)
        file = Path(str(raw["file_path"]))
        line = max(1, int(raw["start_line"]))
        attributes = {key: _json_value(value) for key, value in raw.items() if key != "id"}
        return ContextNode(
            id=f"codegraph:{raw['id']}",
            kind=f"code.{raw['kind']}",
            name=str(raw["name"]),
            attributes=attributes,
            sources=[ContextEvidence(provider="codegraph", file=file, line=line)],
        )

    @staticmethod
    def _edge(row: sqlite3.Row, source_file: Path | None) -> ContextEdge:
        raw = dict(row)
        metadata = _decoded_json(raw.get("metadata"))
        attributes: dict[str, JsonValue] = {
            "line": _json_value(raw.get("line")),
            "column": _json_value(raw.get("col")),
            "provenance": _json_value(raw.get("provenance")),
            "metadata": _json_value(metadata),
        }
        return ContextEdge(
            source=f"codegraph:{raw['source']}",
            target=f"codegraph:{raw['target']}",
            kind=str(raw["kind"]),
            attributes=attributes,
            sources=[
                ContextEvidence(
                    provider="codegraph",
                    file=source_file,
                    line=max(1, int(raw["line"])) if raw.get("line") is not None else None,
                    detail=str(raw["provenance"]) if raw.get("provenance") else None,
                )
            ],
        )


def _decoded_json(value: object) -> object:
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _json_value(value: Any) -> JsonValue:
    return cast(JsonValue, json.loads(json.dumps(value, default=str)))
