"""Immutable destination persistence; progress is deliberately stored elsewhere."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from refinery_ai.migrations.run_store import safe_owned_path, safe_state_root
from refinery_ai.modernization.models import ModernizationIntentPlan


def canonical(plan: ModernizationIntentPlan) -> str:
    return json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def intent_digest(plan: ModernizationIntentPlan) -> str:
    return hashlib.sha256(canonical(plan).encode()).hexdigest()


def render_intent_summary(plan: ModernizationIntentPlan) -> str:
    lines = [
        f"# Modernization {plan.plan_id}",
        "",
        f"Mode: {plan.mode.value}; requested JDK: {plan.requested_jdk}",
        "",
        "## Targets",
    ]
    lines += [f"- {t.module}: {t.component} → {t.version}. {t.rationale}" for t in plan.targets]
    lines += ["", "## Objectives"]
    lines += [f"- {o.id} ({o.disposition}): {o.rationale}" for o in plan.objectives]
    lines += ["", "## Analysis coverage"]
    lines += [f"- {a.analyzer}: {a.status}. {a.reason}" for a in plan.analysis]
    lines += [
        "",
        "## Limitations and blocked work",
        *[f"- {s}" for s in plan.blocked_reasons + plan.limitations],
        "",
        "## Evidence",
    ]
    lines += [
        f"- {e.id}: {e.detail} ({e.source}; {e.retrieved_at.isoformat()})" for e in plan.evidence
    ]
    return "\n".join(lines) + "\n"


class IntentPlanStore:
    def __init__(self, repository: Path, state_directory: Path = Path(".refinery")) -> None:
        state = safe_state_root(repository, state_directory)
        self.root = safe_owned_path(state, "modernization-plans")

    def _path(self, plan_id: str) -> Path:
        if not plan_id.isascii() or not plan_id.isalnum() or len(plan_id) > 128:
            raise ValueError("Invalid modernization plan ID")
        return safe_owned_path(self.root, f"{plan_id}.json")

    def save(self, plan: ModernizationIntentPlan) -> Path:
        # Revalidate model_copy and externally constructed values at the persistence boundary.
        plan = ModernizationIntentPlan.model_validate(plan.model_dump())
        path = self._path(plan.plan_id)
        self.root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if intent_digest(self.load(plan.plan_id)) != intent_digest(plan):
                raise ValueError("Modernization plans are immutable; generate a new plan")
        else:
            payload = {"content_digest": intent_digest(plan), "plan": plan.model_dump(mode="json")}
            with tempfile.NamedTemporaryFile(mode="w", dir=self.root, delete=False) as stream:
                temporary_path = Path(stream.name)
                json.dump(payload, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                try:
                    os.link(temporary_path, path)
                except FileExistsError:
                    if intent_digest(self.load(plan.plan_id)) != intent_digest(plan):
                        raise ValueError("Modernization plans are immutable") from None
            finally:
                temporary_path.unlink(missing_ok=True)
        summary = safe_owned_path(self.root, f"{plan.plan_id}.md")
        temporary = safe_owned_path(self.root, f"{plan.plan_id}.md.tmp")
        temporary.write_text(render_intent_summary(plan))
        temporary.replace(summary)
        return path

    def load(self, plan_id: str) -> ModernizationIntentPlan:
        payload = json.loads(self._path(plan_id).read_text())
        plan = ModernizationIntentPlan.model_validate(payload["plan"])
        if payload.get("content_digest") != intent_digest(plan) or plan.plan_id != plan_id:
            raise ValueError("Modernization plan digest mismatch; regenerate plan")
        return plan
