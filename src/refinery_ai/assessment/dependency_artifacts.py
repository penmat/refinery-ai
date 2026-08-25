"""Persist detailed dependency evidence outside concise human-readable reports."""

from __future__ import annotations

import json
from pathlib import Path

from refinery_ai.assessment.models import Assessment


def write_dependency_artifact(assessment: Assessment) -> Path | None:
    result = assessment.analyzers.get("dependencies")
    if result is None:
        return None
    relative = Path(".refinery/dependencies") / f"osv-findings-{assessment.assessment_id}.json"
    path = assessment.repository.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "1.0",
        "assessmentId": assessment.assessment_id,
        "createdAt": assessment.created_at.isoformat(),
        "repository": str(assessment.repository.root),
        "status": result.status.value,
        "tool": result.tool.model_dump(mode="json"),
        "metrics": dict(result.metrics),
        "findings": [finding.model_dump(mode="json") for finding in result.findings],
        "diagnostics": [diagnostic.model_dump(mode="json") for diagnostic in result.diagnostics],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    result.metrics["reportPath"] = str(relative)
    assessment.metrics["dependencies"] = dict(result.metrics)
    return path
