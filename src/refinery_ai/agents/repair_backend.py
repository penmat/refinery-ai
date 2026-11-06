"""Select a migration repair backend without changing orchestration policy."""

from __future__ import annotations

from refinery_ai.agents.ai_repair import AiRepairAgent
from refinery_ai.agents.pi_repair import PiRepairAgent
from refinery_ai.config import Settings
from refinery_ai.migrations.deterministic_repair import DeterministicMigrationRepairer
from refinery_ai.migrations.execution_state import MigrationPolicy
from refinery_ai.migrations.repair import MigrationRepairStage
from refinery_ai.models import RepairBackend, RepairMode


def validate_repair_policy(settings: Settings) -> None:
    if settings.repair_backend is RepairBackend.PI and settings.repair_mode is RepairMode.LOCAL:
        raise ValueError(
            "pi cannot enforce the strict local repair policy; use configured-provider "
            "with a local model configured in pi, or select the internal backend"
        )


def build_repair_stages(settings: Settings) -> tuple[MigrationRepairStage, ...]:
    """Keep deterministic repair first and instantiate only the selected model adapter."""
    validate_repair_policy(settings)
    stages = (
        MigrationRepairStage(
            "deterministic",
            DeterministicMigrationRepairer(),
            settings.max_deterministic_repair_attempts,
        ),
    )
    if settings.repair_mode is RepairMode.DISABLED:
        return stages
    if settings.repair_backend is RepairBackend.PI:
        model_stage = MigrationRepairStage(
            "pi", PiRepairAgent(settings), settings.max_repair_attempts, uses_model=True
        )
    else:
        model_stage = MigrationRepairStage(
            "AI", AiRepairAgent(settings), settings.max_repair_attempts, uses_model=True
        )
    return (*stages, model_stage)


def migration_policy(settings: Settings) -> MigrationPolicy:
    stages = [("deterministic", settings.max_deterministic_repair_attempts, False)]
    if settings.repair_mode is not RepairMode.DISABLED:
        stages.append(
            (
                "pi" if settings.repair_backend is RepairBackend.PI else "AI",
                settings.max_repair_attempts,
                True,
            )
        )
    return MigrationPolicy(
        repair_mode=settings.repair_mode.value,
        repair_backend=settings.repair_backend.value,
        repair_model=settings.repair_model
        if settings.repair_backend is RepairBackend.INTERNAL
        else None,
        coding_agent_model=settings.coding_agent_model,
        coding_agent_provider=settings.coding_agent_provider,
        pi_executable=settings.pi_executable,
        ollama_base_url=settings.ollama_base_url,
        max_repair_attempts=settings.max_repair_attempts,
        max_deterministic_repair_attempts=settings.max_deterministic_repair_attempts,
        stages=stages,
        timeout=settings.command_timeout_seconds,
        output_limit=settings.max_output_characters,
    )
