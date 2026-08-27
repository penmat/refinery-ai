"""Model Context Protocol server for repository evidence tools."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from refinery_ai.tools import RepositoryEvidenceTools

MCPTransport = Literal["stdio", "http"]


def create_mcp_server(repository: Path, *, host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    """Create a repository-scoped MCP server with all public evidence tools."""

    server = FastMCP(
        "Refinery AI",
        instructions=(
            "Read-only tools for inspecting persisted assessment and context-graph evidence "
            "for a single Java repository. Tool results may report unavailable or incomplete "
            "evidence; do not infer missing facts."
        ),
        host=host,
        port=port,
    )
    evidence = RepositoryEvidenceTools(repository)
    for tool in evidence.tools():
        # FastMCP derives the tool description and argument schema from the method's
        # docstring and annotated signature, preserving the catalog's LLM guidance.
        server.tool()(tool)
    return server


def run_mcp_server(
    repository: Path,
    *,
    transport: MCPTransport = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the repository evidence server over stdio or Streamable HTTP."""

    protocol_transport: Literal["stdio", "streamable-http"] = (
        "stdio" if transport == "stdio" else "streamable-http"
    )
    create_mcp_server(repository, host=host, port=port).run(transport=protocol_transport)
