"""Scoped source migration agent."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from refinery_ai.models import DiscoverySnapshot, PatchProposal


@dataclass(frozen=True)
class MigrationDependencies:
    snapshot: DiscoverySnapshot
    allowed_files: tuple[str, ...]


def create_migration_agent(model: str) -> Agent[MigrationDependencies, PatchProposal]:
    """Create a migration agent that proposes, but never applies, a unified diff."""

    agent: Agent[MigrationDependencies, PatchProposal] = Agent(
        model,
        deps_type=MigrationDependencies,
        output_type=PatchProposal,
        system_prompt=(
            "Propose the smallest correct unified diff for the assigned migration step. "
            "Only touch explicitly allowed files. Do not weaken tests or add unrelated changes."
        ),
    )

    @agent.instructions
    def migration_scope(ctx: RunContext[MigrationDependencies]) -> str:
        return f"Allowed files: {', '.join(ctx.deps.allowed_files)}"

    return agent
