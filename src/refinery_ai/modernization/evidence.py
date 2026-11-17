"""Collect mode-scoped evidence without mutating application sources."""

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from pydantic import Field

from refinery_ai.assessment.engine import AssessmentEngine
from refinery_ai.config import Settings
from refinery_ai.discovery import discover
from refinery_ai.models import DiscoverySnapshot
from refinery_ai.modernization.models import AnalysisAvailability, Contract, EvidenceReference
from refinery_ai.modernization.policy import ModePolicy
from refinery_ai.profile_run import dependency_inputs_fingerprint


class ProjectEvidence(Contract):
    snapshot: DiscoverySnapshot
    revision: str
    build_fingerprint: str
    source_fingerprint: str
    references: list[EvidenceReference] = Field(default_factory=list)
    analysis: list[AnalysisAvailability] = Field(default_factory=list)
    module_versions: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    owners: dict[str, dict[str, Any]] = Field(default_factory=dict)
    inventory: list[dict[str, Any]] = Field(default_factory=list)
    vulnerabilities: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    findings: dict[str, list[str]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


def revision(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def source_fingerprint(repository: Path, state_directory: Path = Path(".refinery")) -> str:
    ignored = {
        ".git",
        ".gradle",
        ".refinery",
        ".codegraph",
        "target",
        "build",
        ".venv",
        "node_modules",
        ".idea",
        ".vscode",
        "graphify-out",
    }
    state = (repository / state_directory).resolve()
    digest = hashlib.sha256()
    for path in sorted(repository.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or ignored.intersection(path.relative_to(repository).parts)
            or path.resolve().is_relative_to(state)
        ):
            continue
        digest.update(path.relative_to(repository).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def collect_evidence(repository: Path, policy: ModePolicy, settings: Settings) -> ProjectEvidence:
    root = repository.expanduser().resolve()
    snapshot = discover(root)
    result = ProjectEvidence(
        snapshot=snapshot,
        revision=revision(root),
        build_fingerprint=dependency_inputs_fingerprint(root),
        source_fingerprint=source_fingerprint(root, settings.state_directory),
        references=[
            EvidenceReference(
                id="discovery",
                source="repository build declarations",
                detail=snapshot.model_dump_json(),
            )
        ],
    )
    from refinery_ai.modernization.owners import maven_owners, pom_owners

    if snapshot.build_system.value == "maven":
        result.owners = {":".join(key): value for key, value in maven_owners(root).items()}
    if snapshot.build_system.value == "maven":
        from refinery_ai.discovery import _maven_details, _spring_boot_parent_version

        # Aggregate discovery can select a child's version. Observe every descriptor itself.
        for build_file in snapshot.build_files:
            if build_file.name != "pom.xml":
                continue
            pom = root / build_file
            _, java = _maven_details(pom)
            declared_owners = pom_owners(pom.read_text())
            boot = _spring_boot_parent_version(pom) or declared_owners.get(
                ("org.springframework.boot", "spring-boot-dependencies"), {}
            ).get("version")
            result.module_versions[str(build_file.parent)] = {"java": java, "spring_boot": boot}
        snapshot.spring_boot_version = next(
            (v["spring_boot"] for v in result.module_versions.values() if v["spring_boot"]), None
        )
        result.references[0].detail = snapshot.model_dump_json()
    else:
        result.module_versions["."] = {
            "java": snapshot.java_version,
            "spring_boot": snapshot.spring_boot_version,
        }
    for analyzer, enabled in [
        ("dependencies", policy.dependencies),
        ("sonarqube", policy.sonarqube),
        ("codegraph", policy.codegraph),
    ]:
        if not enabled:
            result.analysis.append(AnalysisAvailability(analyzer=analyzer, status="off"))
            continue
        try:
            if analyzer == "codegraph":
                from refinery_ai.context_graph.builder import ContextGraphBuilder
                from refinery_ai.context_graph.providers import CodeGraphProvider
                from refinery_ai.context_graph.store import ContextGraphStore

                graph = ContextGraphBuilder(
                    [
                        CodeGraphProvider(
                            timeout=settings.codegraph_timeout_seconds,
                            output_limit=settings.max_output_characters,
                        )
                    ],
                    store=ContextGraphStore(
                        settings.state_directory / "context/context-graph.json"
                    ),
                ).build(root, snapshot)
                result.references.append(
                    EvidenceReference(
                        id="codegraph", source=str(graph.graph_path), detail=graph.summary()
                    )
                )
                provider = next(
                    (p for p in graph.provider_runs if "codegraph" in p.provider.lower()), None
                )
                result.analysis.append(
                    AnalysisAvailability(
                        analyzer=analyzer,
                        status="completed"
                        if provider and provider.status == "completed"
                        else "unavailable",
                        reason="; ".join(provider.diagnostics)
                        if provider
                        else "CodeGraph provider unavailable",
                    )
                )
                continue
            if analyzer == "sonarqube":
                # Do not pull/install services implicitly. Existing local image is required.
                image = subprocess.run(
                    ["docker", "image", "inspect", settings.sonarqube_image],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if image.returncode:
                    raise RuntimeError(
                        "Configured SonarQube image is unavailable; no image installed"
                    )
            assessment = AssessmentEngine(
                inspectors=(),
                settings=settings,
                dependency_analysis_enabled=analyzer == "dependencies",
                sonarqube_enabled=analyzer == "sonarqube",
            ).assess(root)
            analyzed = assessment.analyzers[analyzer]
            status = analyzed.status.value
            result.analysis.append(
                AnalysisAvailability(
                    analyzer=analyzer,
                    status="completed"
                    if status == "completed"
                    else "partial"
                    if status == "partial"
                    else "unavailable",
                    reason="; ".join(d.message for d in analyzed.diagnostics),
                    fingerprint=result.build_fingerprint
                    if analyzer == "dependencies"
                    else result.source_fingerprint,
                )
            )
            result.references.append(
                EvidenceReference(id=analyzer, source=analyzer, detail=analyzed.model_dump_json())
            )
            result.findings[analyzer] = [f.rule for f in analyzed.findings]
            if analyzer == "dependencies":
                result.inventory = analyzed.tool.metadata.get("resolvedDependencies", [])
                result.vulnerabilities = analyzed.tool.metadata.get("vulnerabilityEvidence", {})
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            result.analysis.append(
                AnalysisAvailability(analyzer=analyzer, status="unavailable", reason=str(error))
            )
    result.limitations.extend(
        f"{a.analyzer}: {a.reason or a.status}"
        for a in result.analysis
        if a.status in {"partial", "unavailable"}
    )
    return result
