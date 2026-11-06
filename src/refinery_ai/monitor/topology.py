"""Serialize actual graph structure without invoking workflow operations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from refinery_ai.migrations.run_store import atomic_json, safe_owned_path
from refinery_ai.monitor.models import GraphTopology, TopologyEdge, TopologyNode


def graph_topology(graph: Any, kind: Literal["profile", "migration"]) -> GraphTopology:
    drawing = graph.get_graph()
    nodes = [
        TopologyNode(id=str(key), label=str(key).replace("_", " ").strip())
        for key in sorted(drawing.nodes)
    ]
    edges = [
        TopologyEdge(source=e.source, target=e.target, conditional=e.conditional)
        for e in drawing.edges
    ]
    value = {"nodes": [n.model_dump() for n in nodes], "edges": [e.model_dump() for e in edges]}
    version = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]
    return GraphTopology(kind=kind, version=version, nodes=nodes, edges=edges)


def save_topology(state_root: Path, run_id: str, topology: GraphTopology) -> None:
    path = safe_owned_path(state_root, f"monitor-topologies/{run_id}.json")
    # A resumed invocation keeps its original historical topology.
    if not path.exists():
        atomic_json(path, topology)


def current_topology(kind: Literal["profile", "migration"]) -> GraphTopology:
    if kind == "migration":
        from refinery_ai.workflows.migration import build_migration_graph

        graph = build_migration_graph(None)
    else:
        from refinery_ai.workflows.profile import build_profile_graph

        graph = build_profile_graph(None)  # closures are not invoked when building
    return graph_topology(graph, kind).model_copy(update={"historical": False})
