import sqlite3
import subprocess
from pathlib import Path

import pytest

from refinery_ai.context_graph.builder import ContextGraphBuilder
from refinery_ai.context_graph.contracts import ContextRequest
from refinery_ai.context_graph.providers.codegraph import CodeGraphError, CodeGraphProvider
from refinery_ai.context_graph.providers.codegraph_sql import (
    CodeGraphSchemaError,
    CodeGraphSqlExporter,
)


def completed(command: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def create_database(root: Path) -> Path:
    state = root / ".codegraph"
    state.mkdir()
    database = state / "codegraph.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_versions(version INTEGER PRIMARY KEY);
        INSERT INTO schema_versions VALUES (9);
        CREATE TABLE nodes(
          id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT,
          file_path TEXT, language TEXT, start_line INTEGER, end_line INTEGER,
          start_column INTEGER, end_column INTEGER, signature TEXT
        );
        CREATE TABLE edges(
          id INTEGER PRIMARY KEY, source TEXT, target TEXT, kind TEXT,
          metadata TEXT, line INTEGER, col INTEGER, provenance TEXT
        );
        INSERT INTO nodes VALUES
          ('A', 'class', 'Service', 'example.Service', 'src/Service.java', 'java',
           3, 20, 1, 2, 'class Service'),
          ('B', 'method', 'run', 'example.Service.run', 'src/Service.java', 'java',
           7, 9, 3, 4, 'void run()');
        INSERT INTO edges VALUES
          (1, 'A', 'B', 'contains', '{"resolved":true}', 7, 3, 'ast'),
          (2, 'A', 'missing', 'calls', NULL, NULL, NULL, NULL);
        """
    )
    connection.commit()
    connection.close()
    return database


def test_build_exports_codegraph_sqlite_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_database(tmp_path)
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return completed(command, "codegraph 1.2.3\n" if "--version" in command else "")

    monkeypatch.setattr(subprocess, "run", run)
    info = ContextGraphBuilder([CodeGraphProvider()]).build(tmp_path)

    assert info.node_count == 2
    assert info.edge_count == 1
    assert info.provider == "context-graph"
    assert info.graph_path == tmp_path / ".refinery/context/context-graph.json"
    assert info.provider_runs[0].provider == "codegraph"
    assert commands[0][1:3] == ["index", str(tmp_path)]
    assert commands[1] == ["codegraph", "--version"]


def test_sql_export_preserves_source_and_metadata(tmp_path: Path) -> None:
    database = create_database(tmp_path)

    nodes, edges, version = CodeGraphSqlExporter().export(database)

    assert version == 9
    assert nodes[0].id == "codegraph:A"
    assert nodes[0].sources[0].file == Path("src/Service.java")
    assert nodes[0].sources[0].line == 3
    assert edges[0].attributes["metadata"] == {"resolved": True}
    assert edges[0].sources[0].file == Path("src/Service.java")
    assert edges[0].sources[0].detail == "ast"


def test_missing_codegraph_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(CodeGraphError, match=r"install.*CLI"):
        CodeGraphProvider().collect(ContextRequest(repository=tmp_path))


def test_incompatible_codegraph_schema_is_rejected(tmp_path: Path) -> None:
    state = tmp_path / ".codegraph"
    state.mkdir()
    database = state / "codegraph.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE nodes(id TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(CodeGraphSchemaError, match="missing tables"):
        CodeGraphSqlExporter().export(database)
