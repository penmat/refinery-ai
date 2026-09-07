"""Compatibility imports for the AI repair agent.

New code should import :class:`AiRepairAgent` from ``refinery_ai.agents.ai_repair``.
"""

from refinery_ai.agents.ai_repair import (
    AiRepairAgent,
    RepairEditProposal,
    RepairFileReplacement,
    _ai_repair_files,
    _require_local_endpoint,
)

AiMigrationRepairer = AiRepairAgent


__all__ = [
    "AiMigrationRepairer",
    "AiRepairAgent",
    "RepairEditProposal",
    "RepairFileReplacement",
    "_ai_repair_files",
    "_require_local_endpoint",
]
