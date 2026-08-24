"""Typed Pydantic AI agent boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from refinery_ai.models import (
    DiscoverySnapshot,
    ModernizationPlan,
    PatchProposal,
    RepairProposal,
    VerificationResult,
)


@dataclass(frozen=True)
class PlanningDependencies:
    snapshot: DiscoverySnapshot
    graph_summary: str
    graph_context: str = ""


@dataclass(frozen=True)
class MigrationDependencies:
    snapshot: DiscoverySnapshot
    allowed_files: tuple[str, ...]


@dataclass(frozen=True)
class RepairDependencies(MigrationDependencies):
    failure: VerificationResult
    attempt: int


def create_planning_agent(model: str) -> Agent[PlanningDependencies, ModernizationPlan]:
    """Create a planner that must return a validated modernization plan."""

    agent: Agent[PlanningDependencies, ModernizationPlan] = Agent(
        model,
        deps_type=PlanningDependencies,
        output_type=ModernizationPlan,
        system_prompt=(
            "Plan safe Java modernizations. Prefer deterministic OpenRewrite recipes, then "
            "narrowly scoped AI edits. Every ordered step needs an acceptance check. Never "
            "delete or weaken tests."
        ),
    )

    @agent.instructions
    def project_context(ctx: RunContext[PlanningDependencies]) -> str:
        return (
            f"Discovery: {ctx.deps.snapshot.model_dump_json()}\n"
            f"Knowledge graph: {ctx.deps.graph_summary}\n"
            f"Scoped Graphify context: {ctx.deps.graph_context}"
        )

    return agent


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
