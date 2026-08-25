"""Deterministic bounded retrieval over normalized context graphs."""

from __future__ import annotations

import json
from pathlib import Path

from refinery_ai.context_graph.store import ContextGraphStore


class ContextGraphQuery:
    def __init__(self, store: ContextGraphStore | None = None, *, limit: int = 50) -> None:
        self.store = store or ContextGraphStore()
        self.limit = limit

    def query(self, graph_path: Path, question: str) -> str:
        graph = self.store.load(graph_path)
        terms = {term.casefold() for term in question.split() if len(term) > 2}

        def matches(value: object) -> bool:
            text = json.dumps(value, default=str).casefold()
            return not terms or any(term in text for term in terms)

        nodes = [node for node in graph.nodes if matches(node.model_dump())][: self.limit]
        node_ids = {node.id for node in nodes}
        edges = [
            edge
            for edge in graph.edges
            if edge.source in node_ids or edge.target in node_ids or matches(edge.model_dump())
        ][: self.limit]
        return json.dumps(
            {
                "question": question,
                "nodes": [node.model_dump(mode="json") for node in nodes],
                "edges": [edge.model_dump(mode="json") for edge in edges],
            },
            indent=2,
        )
