"""Human-readable run reports."""

from refinery_ai.models import RunReport


def to_markdown(report: RunReport) -> str:
    lines = [
        f"# Refinery AI run {report.run_id}",
        "",
        f"**Status:** {report.status.value}",
        f"**Repository:** `{report.snapshot.repository}`",
        f"**Goal:** {report.plan.goal.description}",
        f"**Context graph:** `{report.context_graph.graph_path}`",
        f"**Graph size:** {report.context_graph.node_count} nodes, "
        f"{report.context_graph.edge_count} edges",
        f"**Context providers:** "
        f"{', '.join(run.provider for run in report.context_graph.provider_runs) or 'none'}",
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
