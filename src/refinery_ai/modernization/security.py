"""Security candidate queries; unknown compatibility remains explicit."""

import re
from typing import Protocol

from refinery_ai.inspectors.dependencies.models import Vulnerability
from refinery_ai.modernization.versions import VersionCandidate, VersionCandidateProvider


class VulnerabilitySource(Protocol):
    def query(self, purls: list[str]) -> dict[str, list[Vulnerability]]: ...


def remediation_candidates(
    group: str,
    artifact: str,
    current: str,
    versions: VersionCandidateProvider,
    scanner: VulnerabilitySource,
) -> list[VersionCandidate]:
    # Numeric versions can be ordered without pretending Maven qualifiers are SemVer.
    # Other forms require explicit compatibility evidence and are left unresolved.
    if not re.fullmatch(r"\d+(\.\d+)+", current):
        return []
    current_parts = tuple(int(v) for v in current.split("."))
    candidates = [
        v
        for v in versions.candidates(group, artifact)
        if re.fullmatch(r"\d+(\.\d+)+", v.version)
        and tuple(int(n) for n in v.version.split(".")) > current_parts
    ]
    candidates.sort(key=lambda v: tuple(int(n) for n in v.version.split(".")))
    # Keep recent candidates in each release line, including major-only fixes.
    # A long history of vulnerable patches must not hide the available fixed release.
    lines: dict[str, list[VersionCandidate]] = {}
    for candidate in candidates:
        lines.setdefault(candidate.version.split(".")[0], []).append(candidate)
    candidates = [c for line in list(lines.values())[:10] for c in line[-20:]]
    purls = [f"pkg:maven/{group}/{artifact}@{v.version}" for v in candidates]
    findings = scanner.query(purls) if purls else {}
    return [
        v
        for p, v in zip(purls, candidates, strict=True)
        if p in findings and not any(not issue.withdrawn for issue in findings[p])
    ]


def owner_remediation(
    dependency: dict[str, object],
    owners: dict[str, dict[str, object]],
    versions: VersionCandidateProvider,
    scanner: VulnerabilitySource,
    selected: dict[str, str],
) -> tuple[str, VersionCandidate, str] | None:
    """Prove a declared BOM/direct owner's candidate POM removes a transitive advisory.

    Unresolved inheritance or mediated versions are not guessed. Fresh graph/OSV
    verification remains required after execution.
    """
    victim = (str(dependency["group"]), str(dependency["artifact"]))
    path = dependency.get("dependency_path", ())
    direct_owner = (
        ":".join(str(path[0]).split(":")[:2]) if isinstance(path, (list, tuple)) and path else None
    )
    for coordinate, owner in owners.items():
        current = owner.get("version")
        if not isinstance(current, str) or coordinate.startswith("org.springframework.boot:"):
            continue
        is_bom = owner.get("kind") == "bom"
        if not is_bom and (owner.get("kind") != "direct" or coordinate != direct_owner):
            continue
        group, artifact = coordinate.split(":", 1)
        current_versions = versions.managed_versions(
            group, artifact, current, include_direct=not is_bom
        )
        if current_versions.get(victim) != dependency.get("version"):
            continue
        for candidate in remediation_candidates(group, artifact, current, versions, scanner):
            if coordinate in selected and candidate.version != selected[coordinate]:
                continue
            candidate_versions = versions.managed_versions(
                group, artifact, candidate.version, include_direct=not is_bom
            )
            fixed = candidate_versions.get(victim)
            if not fixed or fixed == dependency.get("version"):
                continue
            purl = f"pkg:maven/{victim[0]}/{victim[1]}@{fixed}"
            checked = scanner.query([purl])
            if purl in checked and not any(not v.withdrawn for v in checked[purl]):
                return coordinate, candidate, fixed
    return None
