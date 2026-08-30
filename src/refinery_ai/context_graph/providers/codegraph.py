"""CodeGraph provider for structural code evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

from refinery_ai.context_graph.contracts import ContextFragment, ContextRequest
from refinery_ai.context_graph.providers.codegraph_sql import (
    CodeGraphSchemaError,
    CodeGraphSqlExporter,
)
from refinery_ai.logging import get_logger, log_command

logger = get_logger(__name__)


class CodeGraphError(RuntimeError):
    """Raised when CodeGraph cannot build or export its index."""


class CodeGraphProvider:
    """Index locally with CodeGraph and export its SQLite graph."""

    def __init__(
        self,
        *,
        executable: str = "codegraph",
        timeout: int = 900,
        output_limit: int = 20_000,
        exporter: CodeGraphSqlExporter | None = None,
    ) -> None:
        self.executable = executable
        self.timeout = timeout
        self.output_limit = output_limit
        self.exporter = exporter or CodeGraphSqlExporter()

    @property
    def name(self) -> str:
        return "codegraph"

    def supports(self, discovery: object | None) -> bool:
        return True

    def collect(self, request: ContextRequest) -> ContextFragment:
        root = request.repository.resolve()
        state = root / ".codegraph"
        if state.is_dir():
            self._run([self.executable, "index", str(root), "--force", "--quiet"], cwd=root)
        else:
            self._run([self.executable, "init", str(root), "--yes"], cwd=root)
        database = state / "codegraph.db"
        try:
            nodes, edges, schema_version = self.exporter.export(database)
        except CodeGraphSchemaError as error:
            raise CodeGraphError(str(error)) from error
        version = self._run([self.executable, "--version"], cwd=root).stdout.strip()
        return ContextFragment(
            provider=self.name,
            nodes=nodes,
            edges=edges,
            diagnostics=[
                f"Native CodeGraph artifact: {database}",
                f"CodeGraph CLI: {version or 'unknown'}; schema: {schema_version}",
            ],
        )

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
            raise CodeGraphError(
                "CodeGraph is not installed. Install the `codegraph` CLI and ensure it is on PATH."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise CodeGraphError(f"CodeGraph timed out after {self.timeout} seconds") from error
        if result.returncode != 0:
            output = (result.stderr or result.stdout)[-self.output_limit :]
            raise CodeGraphError(
                f"CodeGraph failed with exit code {result.returncode}: {output}"
            )
        return result
