"""Graphify-backed code knowledge graph integration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from refinery_ai.models import KnowledgeGraphInfo


class KnowledgeGraphError(RuntimeError):
    """Raised when Graphify cannot build or query the code graph."""


class KnowledgeGraphProvider(Protocol):
    def build(self, repository: Path) -> KnowledgeGraphInfo: ...


class GraphifyKnowledgeGraph:
    """Build and query Graphify's deterministic local AST graph."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout: int = 900,
        output_limit: int = 20_000,
    ) -> None:
        self.command_prefix = [executable] if executable else [sys.executable, "-m", "graphify"]
        self.timeout = timeout
        self.output_limit = output_limit

    def build(self, repository: Path) -> KnowledgeGraphInfo:
        root = repository.resolve()
        graph_path = root / "graphify-out" / "graph.json"
        self._run([*self.command_prefix, "extract", ".", "--code-only"], cwd=root)
        if not graph_path.is_file():
            raise KnowledgeGraphError(
                f"Graphify completed without creating its expected graph: {graph_path}"
            )
        try:
            graph = json.loads(graph_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise KnowledgeGraphError(f"Unable to read Graphify output: {error}") from error
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", graph.get("links", []))
        return KnowledgeGraphInfo(
            graph_path=graph_path,
            report_path=self._optional_path(root / "graphify-out" / "GRAPH_REPORT.md"),
            node_count=len(nodes) if isinstance(nodes, list) else 0,
            edge_count=len(edges) if isinstance(edges, list) else 0,
        )

    def query(self, graph_path: Path, question: str) -> str:
        result = self._run(
            [*self.command_prefix, "query", question, "--graph", str(graph_path)],
            cwd=graph_path.parent.parent,
        )
        return result.stdout.strip()

    @staticmethod
    def _optional_path(path: Path) -> Path | None:
        return path if path.is_file() else None

    def _run(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout,
            )
        except FileNotFoundError as error:
            raise KnowledgeGraphError(
                "Graphify is not installed. Run `uv sync` in the Refinery repository."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise KnowledgeGraphError(f"Graphify timed out after {self.timeout} seconds") from error
        if result.returncode != 0:
            output = (result.stderr or result.stdout)[-self.output_limit :]
            if "No module named graphify" in output:
                raise KnowledgeGraphError(
                    "Graphify is not installed. Run `uv sync` in the Refinery repository."
                )
            raise KnowledgeGraphError(
                f"Graphify failed with exit code {result.returncode}: {output}"
            )
        return result
