"""Human-readable project architecture summary rendering."""

from __future__ import annotations

from refinery_ai.summary.models import ProjectSummary


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
    for observation in summary.observations:
        lines.append(f"- [{observation.severity}] {observation.message}")
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
