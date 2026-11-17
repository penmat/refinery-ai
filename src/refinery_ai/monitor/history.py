"""Compact, replayable attempt history from structured migration lifecycle events."""

from refinery_ai.monitor.models import MonitorEvent, NodeExecutionEntry

STEP_PHASES = frozenset(
    {
        "prepare_step",
        "execute_recipe",
        "verify_candidate",
        "select_repair",
        "repair",
        "evaluate_repair",
        "checkpoint_step",
        "record_step_failure",
    }
)


def migration_history(events: list[MonitorEvent]) -> list[NodeExecutionEntry]:
    entries: list[NodeExecutionEntry] = []
    current: dict[tuple[str, int, int], NodeExecutionEntry] = {}
    attempts: dict[str, int] = {}
    seen: set[str] = set()
    for event in events:
        if event.event_id in seen or event.lifecycle == "usage":
            continue
        seen.add(event.event_id)
        if event.phase not in STEP_PHASES:
            # A run-level exception has no recipe identity, but ends pending work.
            if event.lifecycle in ("failed", "cancelled", "interrupted"):
                for pending in current.values():
                    if pending.generation == event.generation and pending.status in (
                        "active",
                        "applied",
                    ):
                        pending.status = event.lifecycle
            continue
        if event.step is None or not event.recipe:
            continue
        key = (event.run_id, event.generation, event.step)
        entry = current.get(key)
        if entry is None or (event.phase == "prepare_step" and event.lifecycle == "started"):
            attempts[event.recipe] = attempts.get(event.recipe, 0) + 1
            entry = NodeExecutionEntry(
                id=f"{event.run_id}:{event.execution_id}",
                run_id=event.run_id,
                parent_execution_id=event.parent_execution_id,
                generation=event.generation,
                step=event.step,
                recipe=event.recipe,
                label=event.migration_name or event.recipe.rsplit(".", 1)[-1],
                attempt=attempts[event.recipe],
            )
            current[key] = entry
            entries.append(entry)
        if event.lifecycle in ("failed", "cancelled", "interrupted"):
            entry.status = event.lifecycle
        elif event.failure_reason or event.outcome in ("verification_failed", "failed"):
            entry.status = "failed"
        elif event.lifecycle == "completed" and event.outcome == "skipped":
            entry.status = "skipped"
        elif event.phase == "record_step_failure":
            entry.status = "failed"
        elif event.lifecycle == "started":
            entry.status = "active"
        elif event.phase == "checkpoint_step" and event.lifecycle == "completed":
            entry.status = (
                "previewed"
                if event.apply is False
                else "verified"
                if event.checkpoint
                else "completed"
            )
        elif event.phase == "execute_recipe" and event.lifecycle == "completed":
            # Legacy records do not establish whether changes were actually applied.
            entry.status = "applied" if event.apply is True else "active"
    return entries
