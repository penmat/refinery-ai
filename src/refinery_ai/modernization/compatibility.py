"""Dated, sourced platform constraints; no inferred support-status claims."""

import json
from pathlib import Path
from typing import Any

from refinery_ai.modernization.models import TargetSelection


class CompatibilityProvider:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = json.loads(
            (Path(__file__).parent / "data/compatibility.json").read_text()
        )

    def supports(self, version: str, target_java: int) -> bool:
        return any(
            e["version"] == version and e["java_min"] <= target_java <= e["java_max"]
            for e in self.entries
        )

    def candidates(self, target_java: int) -> list[TargetSelection]:
        return [
            TargetSelection(
                component="spring_boot",
                version=e["version"],
                evidence_ids=[f"compatibility-{e['version']}"],
                rationale=f"Documented Java {e['java_min']}-{e['java_max']} compatibility",
            )
            for e in self.entries
            if e["java_min"] <= target_java <= e["java_max"]
        ]
