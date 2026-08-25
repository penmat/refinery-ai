"""Kafka context-graph provider."""

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


class KafkaProvider:
    """Collect Kafka clusters, topics, producers, and consumers."""

    name = "kafka"

    def supports(self, discovery: DiscoverySnapshot | None) -> bool:
        return discovery is None or "kafka" in dependencies(discovery)

    def collect(self, request: ContextRequest) -> ContextFragment:
        fragment = ContextFragment(provider=self.name)
        self._collect_configuration(request.repository, fragment)
        self._collect_java(request.repository, fragment)
        return fragment

    def _collect_configuration(self, root: Path, fragment: ContextFragment) -> None:
        bootstrap = re.compile(r"(?:bootstrap-servers|bootstrap\.servers)\s*[:=]\s*([^\s#]+)")
        topic_property = re.compile(r"(?im)^([\w.-]*topic[\w.-]*)\s*[:=]\s*([^\s#]+)")
        for path in files(
            root, ("application*.properties", "application*.yml", "application*.yaml")
        ):
            text = path.read_text(errors="replace")
            for match in bootstrap.finditer(text):
                source = evidence(root, path, text, match.start(), self.name)
                resource(
                    fragment,
                    node_id="kafka:cluster:configured",
                    kind="kafka.cluster",
                    name="configured Kafka cluster",
                    evidence=source,
                    attributes={"bootstrap_servers": "<redacted>"},
                )
            for match in topic_property.finditer(text):
                topic = match.group(2).strip("\"'")
                source = evidence(root, path, text, match.start(), self.name)
                self._topic(fragment, topic, source, attributes={"property": match.group(1)})

    def _collect_java(self, root: Path, fragment: ContextFragment) -> None:
        listener = re.compile(rf"@KafkaListener\s*\([^)]*(?:topics\s*=\s*)?{STRING_LITERAL}")
        send = re.compile(rf"\.send\s*\(\s*{STRING_LITERAL}")
        for path in files(root, ("*.java",)):
            text = path.read_text(errors="replace")
            java_owner = owner(text)
            if java_owner is None:
                continue
            for pattern, edge_kind in ((listener, "consumes"), (send, "publishes")):
                for match in pattern.finditer(text):
                    topic = match.group(1)
                    if topic.startswith("${"):
                        continue
                    source = evidence(root, path, text, match.start(), self.name)
                    self._topic(fragment, topic, source)
                    fragment.edges.append(
                        ContextEdge(
                            source=java_owner,
                            target=f"kafka:topic:{topic}",
                            kind=edge_kind,
                            sources=[source],
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
        resource(
            fragment,
            node_id=f"kafka:topic:{topic}",
            kind="kafka.topic",
            name=topic,
            evidence=evidence,
            attributes=attributes,
        )
