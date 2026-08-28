"""Bounded verification repair agent."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from refinery_ai.agents.migration import MigrationDependencies
from refinery_ai.models import RepairProposal, VerificationResult


@dataclass(frozen=True)
class RepairDependencies(MigrationDependencies):
    failure: VerificationResult
    attempt: int


def create_repair_agent(model: str) -> Agent[RepairDependencies, RepairProposal]:
    """Create a bounded repair agent supplied with the latest verification failure."""

    agent: Agent[RepairDependencies, RepairProposal] = Agent(
        model,
        deps_type=RepairDependencies,
        output_type=RepairProposal,
        system_prompt=(
            "Diagnose the verification failure and propose the smallest scoped repair diff. "
            "Do not suppress checks, remove tests, or modify files outside the allowed set."
        ),
    )

    @agent.instructions
    def failure_context(ctx: RunContext[RepairDependencies]) -> str:
        return (
            f"Attempt: {ctx.deps.attempt}\nAllowed files: {ctx.deps.allowed_files}\n"
            f"Failure: {ctx.deps.failure.model_dump_json()}"
        )

    return agent
