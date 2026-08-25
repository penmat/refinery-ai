"""Spring-specific semantic context-graph provider."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from refinery_ai.context_graph.contracts import ContextFragment, ContextRequest
from refinery_ai.context_graph.models import ContextEdge, ContextEvidence, ContextNode
from refinery_ai.models import DiscoverySnapshot

_IGNORED_PARTS = {".git", ".gradle", ".idea", ".refinery", "build", "target", "graphify-out"}
_ANNOTATION = re.compile(r"@(?P<name>[\w.]+)(?:\s*\((?P<args>[^)]*)\))?")
_TYPE = re.compile(
    r"(?P<annotations>(?:@[\w.]+(?:\s*\([^)]*\))?\s*)*)"
    r"(?:(?:public|protected|private|abstract|final|static)\s+)*"
    r"(?P<declaration>class|interface|record)\s+(?P<name>\w+)"
    r"(?P<tail>[^\{]*)\{",
    re.MULTILINE,
)
_METHOD = re.compile(
    r"(?P<annotations>(?:@[\w.]+(?:\s*\([^)]*\))?\s*)+)"
    r"(?:(?:public|protected|private|static|final|abstract|synchronized)\s+)*"
    r"(?P<return>[\w.<>, ?\[\]]+)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class _JavaType:
    name: str
    qualified_name: str
    start: int
    end: int
    annotations: dict[str, str]
    tail: str


class SpringProvider:
    """Collect Spring framework semantics that are not represented by a plain AST graph."""

    name = "spring"

    def supports(self, discovery: DiscoverySnapshot | None) -> bool:
        return discovery is None or "Spring" in discovery.frameworks

    def collect(self, request: ContextRequest) -> ContextFragment:
        fragment = ContextFragment(provider=self.name)
        for path in sorted(request.repository.rglob("*.java")):
            if _IGNORED_PARTS.intersection(path.relative_to(request.repository).parts):
                continue
            self._collect_file(request.repository, path, fragment)
        return fragment

    def _collect_file(self, root: Path, path: Path, fragment: ContextFragment) -> None:
        text = path.read_text(errors="replace")
        relative = path.relative_to(root)
        package_match = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
        package = package_match.group(1) if package_match else ""
        types = self._types(text, package)
        for java_type in types:
            evidence = self._evidence(relative, text, java_type.start)
            self._collect_type(java_type, evidence, fragment)
        for method in _METHOD.finditer(text):
            owner = self._owner(types, method.start())
            if owner is not None:
                self._collect_method(owner, method, relative, text, fragment)
        for owner in types:
            self._collect_injection(owner, text, relative, package, fragment)

    def _collect_type(
        self, java_type: _JavaType, evidence: ContextEvidence, fragment: ContextFragment
    ) -> None:
        component = self._first(
            java_type.annotations,
            "Component",
            "Service",
            "Repository",
            "Controller",
            "RestController",
            "Configuration",
        )
        if component:
            bean_name = self._string_value(java_type.annotations[component]) or self._bean_name(
                java_type.name
            )
            bean_id = f"spring:bean:{bean_name}"
            fragment.nodes.append(
                ContextNode(
                    id=bean_id,
                    kind="spring.bean",
                    name=bean_name,
                    attributes={"stereotype": component, "class": java_type.qualified_name},
                    sources=[evidence],
                )
            )
            fragment.edges.append(
                ContextEdge(
                    source=bean_id,
                    target=f"java:type:{java_type.qualified_name}",
                    kind="implemented_by",
                    sources=[evidence],
                )
            )
        if "ConfigurationProperties" in java_type.annotations:
            arguments = java_type.annotations["ConfigurationProperties"]
            prefix = self._named_value(arguments, "prefix") or self._string_value(arguments)
            if prefix:
                self._configuration_edge(
                    self._owner_id(java_type),
                    prefix,
                    evidence,
                    fragment,
                    kind="binds_configuration",
                )
        if "Entity" in java_type.annotations:
            table = self._named_value(java_type.annotations.get("Table", ""), "name")
            table = table or java_type.name
            table_id = f"database:table:{table}"
            fragment.nodes.append(
                ContextNode(
                    id=table_id,
                    kind="database.table",
                    name=table,
                    attributes={"entity": java_type.qualified_name},
                    sources=[evidence],
                )
            )
            fragment.edges.append(
                ContextEdge(
                    source=f"java:type:{java_type.qualified_name}",
                    target=table_id,
                    kind="maps_to",
                    sources=[evidence],
                )
            )
        repository = re.search(r"(?:JpaRepository|CrudRepository)<\s*([\w.]+)", java_type.tail)
        if repository:
            entity = self._qualify(repository.group(1), java_type.qualified_name.rpartition(".")[0])
            fragment.edges.append(
                ContextEdge(
                    source=f"java:type:{java_type.qualified_name}",
                    target=f"java:type:{entity}",
                    kind="manages_entity",
                    sources=[evidence],
                )
            )

    def _collect_method(
        self,
        owner: _JavaType,
        method: re.Match[str],
        relative: Path,
        text: str,
        fragment: ContextFragment,
    ) -> None:
        annotations = self._annotations(method.group("annotations"))
        evidence = self._evidence(relative, text, method.start())
        owner_id = self._owner_id(owner)
        mapping = self._first(
            annotations,
            "RequestMapping",
            "GetMapping",
            "PostMapping",
            "PutMapping",
            "PatchMapping",
            "DeleteMapping",
        )
        if mapping:
            base = self._mapping_path(owner.annotations.get("RequestMapping", ""))
            path = self._join_path(base, self._mapping_path(annotations[mapping]))
            verb = (
                self._request_method(annotations[mapping])
                if mapping == "RequestMapping"
                else mapping.removesuffix("Mapping").upper()
            )
            endpoint_id = f"http:endpoint:{verb}:{path}"
            fragment.nodes.append(
                ContextNode(
                    id=endpoint_id,
                    kind="http.endpoint",
                    name=f"{verb} {path}",
                    attributes={"method": verb, "path": path},
                    sources=[evidence],
                )
            )
            fragment.edges.append(
                ContextEdge(source=owner_id, target=endpoint_id, kind="handles", sources=[evidence])
            )
        if "Bean" in annotations:
            name = self._string_value(annotations["Bean"]) or method.group("name")
            fragment.nodes.append(
                ContextNode(
                    id=f"spring:bean:{name}",
                    kind="spring.bean",
                    name=name,
                    attributes={"factory_method": method.group("name")},
                    sources=[evidence],
                )
            )
            fragment.edges.append(
                ContextEdge(
                    source=owner_id,
                    target=f"spring:bean:{name}",
                    kind="declares",
                    sources=[evidence],
                )
            )
        self._annotation_resource(
            owner_id,
            annotations,
            "KafkaListener",
            "topics",
            "kafka.topic",
            "consumes",
            evidence,
            fragment,
        )
        for annotation, kind in (
            ("Cacheable", "reads_cache"),
            ("CachePut", "writes_cache"),
            ("CacheEvict", "evicts_cache"),
        ):
            self._annotation_resource(
                owner_id,
                annotations,
                annotation,
                "cacheNames",
                "cache.region",
                kind,
                evidence,
                fragment,
            )

    def _collect_injection(
        self,
        owner: _JavaType,
        text: str,
        relative: Path,
        package: str,
        fragment: ContextFragment,
    ) -> None:
        body = text[owner.start : owner.end]
        field_pattern = re.compile(
            r"@(?:Autowired|Inject|Resource)(?:\([^)]*\))?\s+"
            r"(?:(?:private|protected|public|final)\s+)*(?P<type>[\w.]+)\s+\w+\s*;"
        )
        injected: set[str] = set()
        for match in field_pattern.finditer(body):
            injected.add(match.group("type"))
            self._injection_edge(
                owner,
                match.group("type"),
                relative,
                text,
                owner.start + match.start(),
                package,
                fragment,
            )
        value_pattern = re.compile(r'@Value\s*\(\s*["\']\$\{([^}:]+)(?::[^}]*)?\}["\']\s*\)')
        for match in value_pattern.finditer(body):
            self._configuration_edge(
                self._owner_id(owner),
                match.group(1),
                self._evidence(relative, text, owner.start + match.start()),
                fragment,
            )
        constructor = re.compile(rf"\b{re.escape(owner.name)}\s*\((?P<params>[^)]*)\)")
        for match in constructor.finditer(body):
            for parameter in match.group("params").split(","):
                cleaned = re.sub(r"@[\w.]+(?:\([^)]*\))?", "", parameter).strip()
                parts = cleaned.split()
                if len(parts) >= 2 and parts[-2] not in injected:
                    self._injection_edge(
                        owner,
                        parts[-2],
                        relative,
                        text,
                        owner.start + match.start(),
                        package,
                        fragment,
                    )

    def _injection_edge(
        self,
        owner: _JavaType,
        dependency: str,
        relative: Path,
        text: str,
        position: int,
        package: str,
        fragment: ContextFragment,
    ) -> None:
        simple = re.sub(r"<.*>", "", dependency).removesuffix("[]")
        if simple in {"String", "int", "long", "boolean", "double", "float"}:
            return
        fragment.edges.append(
            ContextEdge(
                source=self._owner_id(owner),
                target=f"java:type:{self._qualify(simple, package)}",
                kind="injects",
                sources=[self._evidence(relative, text, position)],
            )
        )

    @staticmethod
    def _configuration_edge(
        owner_id: str,
        property_name: str,
        evidence: ContextEvidence,
        fragment: ContextFragment,
        *,
        kind: str = "reads_configuration",
    ) -> None:
        property_id = f"configuration:property:{property_name}"
        fragment.nodes.append(
            ContextNode(
                id=property_id,
                kind="configuration.property",
                name=property_name,
                sources=[evidence],
            )
        )
        fragment.edges.append(
            ContextEdge(
                source=owner_id,
                target=property_id,
                kind=kind,
                sources=[evidence],
            )
        )

    def _annotation_resource(
        self,
        owner_id: str,
        annotations: dict[str, str],
        annotation: str,
        named_key: str,
        node_kind: str,
        edge_kind: str,
        evidence: ContextEvidence,
        fragment: ContextFragment,
    ) -> None:
        if annotation not in annotations:
            return
        value = self._named_value(annotations[annotation], named_key) or self._string_value(
            annotations[annotation]
        )
        if not value:
            return
        prefix = "kafka:topic" if node_kind == "kafka.topic" else "cache:region"
        resource_id = f"{prefix}:{value}"
        fragment.nodes.append(
            ContextNode(id=resource_id, kind=node_kind, name=value, sources=[evidence])
        )
        fragment.edges.append(
            ContextEdge(source=owner_id, target=resource_id, kind=edge_kind, sources=[evidence])
        )

    @staticmethod
    def _types(text: str, package: str) -> list[_JavaType]:
        matches = list(_TYPE.finditer(text))
        result: list[_JavaType] = []
        for index, match in enumerate(matches):
            name = match.group("name")
            result.append(
                _JavaType(
                    name=name,
                    qualified_name=f"{package}.{name}" if package else name,
                    start=match.start(),
                    end=matches[index + 1].start() if index + 1 < len(matches) else len(text),
                    annotations=SpringProvider._annotations(match.group("annotations")),
                    tail=match.group("tail"),
                )
            )
        return result

    @staticmethod
    def _annotations(value: str) -> dict[str, str]:
        return {
            match.group("name").rsplit(".", 1)[-1]: match.group("args") or ""
            for match in _ANNOTATION.finditer(value)
        }

    @staticmethod
    def _owner(types: list[_JavaType], position: int) -> _JavaType | None:
        return next((item for item in reversed(types) if item.start <= position < item.end), None)

    @staticmethod
    def _first(values: dict[str, str], *names: str) -> str | None:
        return next((name for name in names if name in values), None)

    @staticmethod
    def _string_value(value: str) -> str | None:
        match = re.search(r'["\']([^"\']+)["\']', value)
        return match.group(1) if match else None

    @staticmethod
    def _named_value(value: str, name: str) -> str | None:
        match = re.search(rf"\b{re.escape(name)}\s*=\s*(?:\{{\s*)?[\"']([^\"']+)", value)
        return match.group(1) if match else None

    @staticmethod
    def _mapping_path(value: str) -> str:
        for name in ("path", "value"):
            match = re.search(rf"\b{name}\s*=\s*(?:\{{\s*)?[\"']([^\"']*)", value)
            if match:
                return match.group(1)
        return SpringProvider._string_value(value) or ""

    @staticmethod
    def _request_method(value: str) -> str:
        match = re.search(r"RequestMethod\.(\w+)", value)
        return match.group(1) if match else "ANY"

    @staticmethod
    def _join_path(base: str, path: str) -> str:
        joined = "/".join(part.strip("/") for part in (base, path) if part.strip("/"))
        return f"/{joined}" if joined else "/"

    @staticmethod
    def _bean_name(name: str) -> str:
        return name[:1].lower() + name[1:]

    @staticmethod
    def _qualify(name: str, package: str) -> str:
        return name if "." in name or not package else f"{package}.{name}"

    @staticmethod
    def _owner_id(owner: _JavaType) -> str:
        component = SpringProvider._first(
            owner.annotations,
            "Component",
            "Service",
            "Repository",
            "Controller",
            "RestController",
            "Configuration",
        )
        if component:
            name = SpringProvider._string_value(
                owner.annotations[component]
            ) or SpringProvider._bean_name(owner.name)
            return f"spring:bean:{name}"
        return f"java:type:{owner.qualified_name}"

    @staticmethod
    def _evidence(path: Path, text: str, position: int) -> ContextEvidence:
        return ContextEvidence(provider="spring", file=path, line=text.count("\n", 0, position) + 1)
