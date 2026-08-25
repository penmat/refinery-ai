"""Graphify provider for structural code evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from refinery_ai.context_graph.contracts import ContextFragment, ContextRequest
from refinery_ai.context_graph.models import (
    ContextEdge,
    ContextEvidence,
    ContextNode,
)
from refinery_ai.logging import get_logger, log_command

logger = get_logger(__name__)


class GraphifyError(RuntimeError):
    """Raised when Graphify cannot build or query the code graph."""


class GraphifyProvider:
    """Collect Graphify's deterministic local AST graph as normalized context."""

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

    @property
    def name(self) -> str:
        return "graphify"

    def supports(self, discovery: object | None) -> bool:
        return True

    def collect(self, request: ContextRequest) -> ContextFragment:
        root = request.repository.resolve()
        graph_path = root / "graphify-out" / "graph.json"
        self._run([*self.command_prefix, "extract", ".", "--code-only"], cwd=root)
        if not graph_path.is_file():
            raise GraphifyError(
                f"Graphify completed without creating its expected graph: {graph_path}"
            )
        try:
            graph = json.loads(graph_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise GraphifyError(f"Unable to read Graphify output: {error}") from error
        candidate_nodes = graph.get("nodes", [])
        candidate_edges = graph.get("edges", graph.get("links", []))
        raw_nodes = candidate_nodes if isinstance(candidate_nodes, list) else []
        raw_edges = candidate_edges if isinstance(candidate_edges, list) else []
        nodes = [self._node(item, index) for index, item in enumerate(raw_nodes)]
        edges = [self._edge(item) for item in raw_edges]
        return ContextFragment(
            provider=self.name,
            nodes=nodes,
            edges=edges,
            diagnostics=[f"Native Graphify artifact: {graph_path}"],
        )

    @staticmethod
    def _node(value: object, index: int) -> ContextNode:
        item = value if isinstance(value, dict) else {"value": value}
        raw_id = item.get("id", item.get("name", index))
        node_id = f"graphify:{raw_id}"
        kind = str(item.get("type", item.get("kind", "symbol")))
        name = str(item.get("name", item.get("label", raw_id)))
        attributes = json.loads(json.dumps(item, default=str))
        return ContextNode(
            id=node_id,
            kind=f"code.{kind}",
            name=name,
            attributes=attributes,
            sources=[ContextEvidence(provider="graphify")],
        )

    @staticmethod
    def _edge(value: object) -> ContextEdge:
        item = value if isinstance(value, dict) else {"value": value}
        source = item.get("source", item.get("from", "unknown"))
        target = item.get("target", item.get("to", "unknown"))
        kind = str(item.get("type", item.get("kind", "related")))
        attributes = json.loads(json.dumps(item, default=str))
        return ContextEdge(
            source=f"graphify:{source}",
            target=f"graphify:{target}",
            kind=kind,
            attributes=attributes,
            sources=[ContextEvidence(provider="graphify")],
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
        log_command(logger, command, cwd=cwd)
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
            raise GraphifyError(
                "Graphify is not installed. Run `uv sync` in the Refinery repository."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise GraphifyError(f"Graphify timed out after {self.timeout} seconds") from error
        if result.returncode != 0:
            output = (result.stderr or result.stdout)[-self.output_limit :]
            if "No module named graphify" in output:
                raise GraphifyError(
                    "Graphify is not installed. Run `uv sync` in the Refinery repository."
                )
            raise GraphifyError(f"Graphify failed with exit code {result.returncode}: {output}")
        return result
