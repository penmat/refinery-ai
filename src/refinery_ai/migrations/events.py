"""Safe migration lifecycle envelopes and append-only local replay."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langgraph.config import get_stream_writer
from pydantic import BaseModel

from refinery_ai.migrations.execution_state import MigrationExecutionState


class MigrationEvent(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    plan_id: str
    repository: str
    profile: str | None
    sequence: int
    timestamp: datetime
    event_id: str
    execution_id: str
    phase: str
    lifecycle: Literal["started", "completed", "failed", "cancelled", "interrupted", "usage"]
    generation: int
    step: int | None = None
    recipe: str | None = None
    repair_stage: str | None = None
    repair_attempt: int | None = None
    java: int | None = None
    duration_seconds: float | None = None
    outcome: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    checkpoint: str | None = None
    parent_run_id: str | None = None
    parent_execution_id: str | None = None


def read_migration_events(path: Path) -> list[MigrationEvent]:
    data = path.read_bytes() if path.exists() else b""
    events: list[MigrationEvent] = []
    lines = data.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            payload = json.loads(line)
        except (ValueError, UnicodeError):
            if index == len(lines) - 1 and not line.endswith(b"\n"):
                break
            raise ValueError("Corrupt complete migration event record") from None
        event = MigrationEvent.model_validate(payload)
        if event.sequence != len(events) + 1:
            raise ValueError("Invalid migration event sequence")
        events.append(event)
    return events


class MigrationReporter:
    def __init__(
        self,
        path: Path | None,
        observer: Callable[[MigrationEvent], None] | None,
        stream_writer: Callable[[Any], None] | None = None,
    ) -> None:
        self.stream_writer = stream_writer
        self.observer = observer
        self.history = read_migration_events(path) if path is not None else []
        self.sequence = len(self.history)
        self.output = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise ValueError("Migration journal must not be a symlink")
            if path.exists():
                data = path.read_bytes()
                if data and not data.endswith(b"\n"):
                    try:
                        json.loads(data.split(b"\n")[-1])
                    except (ValueError, UnicodeError):
                        data = data[: data.rfind(b"\n") + 1]
                    else:
                        data += b"\n"
                    path.write_bytes(data)
            self.output = path.open("a")

    def emit(
        self,
        phase: str,
        lifecycle: Literal["started", "completed", "failed", "cancelled", "interrupted", "usage"],
        state: MigrationExecutionState,
        execution_id: str,
        *,
        duration: float | None = None,
        outcome: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        checkpoint: str | None = None,
        stage: str | None = None,
    ) -> MigrationEvent:
        recipe = (
            state.plan.steps[state.index - 1].recipe
            if 0 < state.index <= len(state.plan.steps)
            else None
        )
        from refinery_ai.monitor.recording import current_invocation

        parent = current_invocation.get()
        event = MigrationEvent(
            parent_run_id=parent.run_id if parent else None,
            parent_execution_id=parent.execution_id if parent else None,
            run_id=state.run_id,
            plan_id=state.plan.plan_id,
            repository=str(state.plan.snapshot.repository),
            profile=state.plan.profile,
            sequence=self.sequence + 1,
            timestamp=datetime.now(UTC),
            event_id=uuid4().hex,
            execution_id=execution_id,
            phase=phase,
            lifecycle=lifecycle,
            generation=state.generation,
            step=state.index if recipe is not None else None,
            recipe=recipe,
            repair_stage=stage,
            repair_attempt=state.attempt or None,
            java=state.current_java,
            duration_seconds=duration,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            checkpoint=checkpoint,
        )
        if self.output is not None:
            self.output.write(event.model_dump_json() + "\n")
            self.output.flush()
            os.fsync(self.output.fileno())
        self.sequence += 1
        if self.observer is not None:
            try:
                self.observer(event)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Migration observer failed; persisted reporting continues"
                )
        try:
            writer = self.stream_writer or get_stream_writer()
            writer(event.model_dump(mode="json"))
        except RuntimeError:
            pass  # Direct reporter use outside a graph has no stream context.
        return event

    def close(self) -> None:
        if self.output is not None:
            self.output.close()
