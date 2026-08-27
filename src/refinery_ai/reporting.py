"""Human-readable run reports."""

from refinery_ai.models import ModernizationPlan, RunReport


def to_markdown(report: RunReport) -> str:
    lines = [
        f"# Refinery AI run {report.run_id}",
        "",
        f"**Status:** {report.status.value}",
        f"**Repository:** `{report.snapshot.repository}`",
        f"**Context graph:** `{report.context_graph.graph_path}`",
        f"**Graph size:** {report.context_graph.node_count} nodes, "
        f"{report.context_graph.edge_count} edges",
        f"**Context providers:** "
        f"{', '.join(run.provider for run in report.context_graph.provider_runs) or 'none'}",
        "",
        "## Plan",
        "",
    ]
    if not isinstance(report.plan, ModernizationPlan):
        lines[-2] = "## Recipe recommendations"
        for recommendation in report.plan.recommendations:
            lines.extend(
                [
                    f"- **{recommendation.recipe}** — {recommendation.priority.value}; "
                    f"confidence {recommendation.confidence:.0%}",
                    f"  - {recommendation.reason}",
                ]
            )
        if not report.plan.recommendations:
            lines.append("No recipes had sufficient project evidence.")
        lines.extend(["", "No recipes have been executed."])
        return "\n".join(lines).rstrip() + "\n"
    lines.insert(4, f"**Goal:** {report.plan.goal.description}")
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
