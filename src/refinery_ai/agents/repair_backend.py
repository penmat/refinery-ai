"""Select a migration repair backend without changing orchestration policy."""

from __future__ import annotations

from refinery_ai.agents.ai_repair import AiRepairAgent
from refinery_ai.agents.pi_repair import PiRepairAgent
from refinery_ai.config import Settings
from refinery_ai.migrations.deterministic_repair import DeterministicMigrationRepairer
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
