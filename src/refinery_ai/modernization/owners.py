"""Read declared Maven owners without overriding managed transitive versions."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def pom_owners(content: str) -> dict[tuple[str, str], dict[str, Any]]:
    root = ET.fromstring(content)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    properties = {p.tag: (p.text or "").strip() for p in root.findall("./properties/*")}

    def resolve(value: str | None) -> str | None:
        seen: set[str] = set()
        while value and (match := re.fullmatch(r"\$\{([^}]+)\}", value)):
            if match[1] in seen:
                return None
            seen.add(match[1])
            value = properties.get(match[1])
        return value if value and "${" not in value else None

    owners: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, path in [
        ("parent", "./parent"),
        ("managed", "./dependencyManagement/dependencies/dependency"),
        ("direct", "./dependencies/dependency"),
    ]:
        for node in root.findall(path):
            group, artifact = node.findtext("groupId"), node.findtext("artifactId")
            if not group or not artifact:
                continue
            raw = node.findtext("version")
            match = re.fullmatch(r"\$\{([^}]+)\}", raw or "")
            owners[(group, artifact)] = {
                "kind": "bom" if node.findtext("scope") == "import" else kind,
                "version": resolve(raw),
                "property": match[1] if match else None,
            }
    return owners


def maven_owners(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for pom in sorted(root.rglob("pom.xml")):
        if {".git", ".refinery", "target", "build"}.intersection(pom.relative_to(root).parts):
            continue
        try:
            owners = pom_owners(pom.read_text())
        except (OSError, ET.ParseError):
            continue
        for key, value in owners.items():
            value["module"] = str(pom.parent.relative_to(root))
            if key in result and result[key].get("version") != value.get("version"):
                value = {"kind": "ambiguous", "version": None, "module": "multiple"}
            result[key] = value
    return result
