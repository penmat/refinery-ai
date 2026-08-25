"""Human-readable project architecture summary rendering."""

from __future__ import annotations

from refinery_ai.summary.models import ProjectSummary

_CATEGORY_LABELS = {
    "platform": "Platform Modernization",
    "dependencies": "Dependency Health",
    "maintainability": "Maintainability",
    "risk": "Defect & Security Risk",
    "architecture": "Architecture Health",
    "tests": "Test Confidence",
    "build": "Build & Delivery Health",
}


def summary_markdown(summary: ProjectSummary, *, terminal: bool = False) -> str:
    heading = "Project Architecture Summary" if terminal else "# Project Architecture Summary"
    section = (lambda value: value) if terminal else (lambda value: f"## {value}")
    overview = summary.overview
    lines = [
        heading,
        "=" * len(heading) if terminal else "",
        "",
        f"Repository: {summary.repository}",
        f"Architecture: {overview.architecture_style}",
        f"Build: {overview.build_system}",
        f"Java: {overview.java_version or 'unknown'}",
        f"Frameworks: {', '.join(overview.frameworks) or 'none detected'}",
        f"Modules: {overview.module_count}",
        f"Production/test Java files: {overview.production_java_files}/{overview.test_java_files}",
        "",
        section("Architecture"),
        "",
        f"Primary flow: {summary.architecture.primary_flow or 'not confidently inferred'}",
    ]
    for kind, count in summary.architecture.components_by_kind.items():
        if not kind.startswith("code."):
            lines.append(f"- {kind}: {count}")
    lines.extend(["", section("External interfaces"), ""])
    lines.append(f"- HTTP endpoints: {len(summary.interfaces.http_endpoints)}")
    lines.extend(f"  - {endpoint}" for endpoint in summary.interfaces.http_endpoints[:20])
    lines.append(f"- Kafka topics: {', '.join(summary.interfaces.kafka_topics) or 'none detected'}")
    lines.append(
        f"- Cache regions: {', '.join(summary.interfaces.cache_regions) or 'none detected'}"
    )
    lines.append(
        f"- Redis keyspaces: {', '.join(summary.interfaces.redis_keyspaces) or 'none detected'}"
    )
    lines.extend(["", section("Data architecture"), ""])
    lines.append(f"- Engines: {', '.join(summary.data.engines) or 'none detected'}")
    lines.append(f"- Tables: {len(summary.data.tables)}")
    lines.append(f"- Entity mappings: {summary.data.entity_mappings}")
    lines.append(f"- Table references: {summary.data.table_references}")
    lines.append(
        f"- Direct SQL access: {summary.data.direct_reads} reads, "
        f"{summary.data.direct_writes} writes"
    )
    lines.extend(["", section("Prominent components"), ""])
    lines.extend(
        f"- {item.name} ({item.kind}) — {item.relationship_count} relationships"
        for item in summary.architecture.prominent_components
    )
    score = summary.modernization.score
    lines.extend(["", section("Modernization posture"), ""])
    lines.append(f"- Score: {'not assessed' if score is None else f'{score:g}/100'}")
    lines.append(f"- Evidence completeness: {summary.modernization.evidence_completeness:.0%}")
    lines.append("- Category scores:")
    for category, label in _CATEGORY_LABELS.items():
        value = summary.modernization.category_scores.get(category)
        rendered = "not assessed" if value is None else f"{value:g}/100"
        lines.append(f"  - {label}: {rendered}")
    for observation in summary.observations:
        lines.append(f"- [{observation.severity}] {observation.message}")
    dependency_status = summary.modernization.analyzer_statuses.get("dependencies")
    if dependency_status is not None:
        dependency_heading = "Dependency analysis" if terminal else "### Dependency analysis"
        lines.extend(["", dependency_heading, ""])
        lines.append(f"- Status: {dependency_status}")
        dependency_metrics = summary.modernization.metrics.get("dependencies", {})
        if isinstance(dependency_metrics, dict) and dependency_status == "completed":
            lines.append(
                f"- Resolved dependencies: {dependency_metrics.get('dependencyCount', 0)} "
                f"({dependency_metrics.get('directDependencyCount', 0)} direct)"
            )
            lines.append(
                "- Vulnerable dependencies: "
                f"{dependency_metrics.get('vulnerableDependencyCount', 0)}"
            )
            lines.append(
                f"- OSV findings: {dependency_metrics.get('vulnerabilityCount', 0)} "
                "("
                f"{dependency_metrics.get('criticalVulnerabilityCount', 0)} critical, "
                f"{dependency_metrics.get('highVulnerabilityCount', 0)} high, "
                f"{dependency_metrics.get('mediumVulnerabilityCount', 0)} medium, "
                f"{dependency_metrics.get('lowVulnerabilityCount', 0)} low, "
                f"{dependency_metrics.get('unknownVulnerabilityCount', 0)} unknown)"
            )
            dependency_findings = summary.modernization.findings_by_category.get("dependencies", [])
            lines.extend(f"  - {finding}" for finding in dependency_findings[:20])
            if len(dependency_findings) > 20:
                lines.append(f"  - … and {len(dependency_findings) - 20} more")
        for diagnostic in summary.modernization.analyzer_diagnostics.get("dependencies", []):
            lines.append(f"- Diagnostic: {diagnostic}")
    lines.extend(["", section("Evidence quality"), ""])
    lines.append(f"- Context graph: {summary.evidence.graph_path}")
    lines.append(
        f"- Graph size: {summary.evidence.node_count} nodes, {summary.evidence.edge_count} edges"
    )
    for run in summary.evidence.provider_runs:
        lines.append(
            f"- {run.provider}: {run.status} ({run.node_count} nodes, {run.edge_count} edges)"
        )
    return "\n".join(lines).rstrip() + "\n"
