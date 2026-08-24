"""Human-readable run reports."""

from refinery_ai.models import RunReport


def to_markdown(report: RunReport) -> str:
    lines = [
        f"# Refinery AI run {report.run_id}",
        "",
        f"**Status:** {report.status.value}",
        f"**Repository:** `{report.snapshot.repository}`",
        f"**Goal:** {report.plan.goal.description}",
        f"**Knowledge graph:** `{report.knowledge_graph.graph_path}`",
        f"**Graph size:** {report.knowledge_graph.node_count} nodes, "
        f"{report.knowledge_graph.edge_count} edges",
        "",
        "## Plan",
        "",
    ]
    results = {result.step_id: result for result in report.steps}
    for step in report.plan.steps:
        result = results.get(step.id)
        status = result.status.value if result else "pending"
        lines.append(f"- **{step.title}** — {step.strategy.value}; {status}")
    lines.extend(["", "## Evidence", ""])
    if not report.steps:
        lines.append("No steps have been executed.")
    for result in report.steps:
        lines.append(f"### {result.step_id}")
        lines.append("")
        lines.append(result.message)
        if result.verification:
            command = " ".join(result.verification.command)
            lines.append(f"- Command: `{command}`")
            lines.append(f"- Exit code: `{result.verification.exit_code}`")
            lines.append(f"- Duration: `{result.verification.duration_seconds:.2f}s`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
