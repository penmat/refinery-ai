"""Profile journals and parent/child correlation across graph invocations."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from refinery_ai.migrations.run_store import safe_owned_path
from refinery_ai.monitor.models import MonitorEvent


@dataclass(frozen=True)
class InvocationContext:
    run_id: str
    execution_id: str


current_invocation: ContextVar[InvocationContext | None] = ContextVar(
    "monitor_parent", default=None
)


@contextmanager
def parent_invocation(value: InvocationContext) -> Iterator[None]:
    token = current_invocation.set(value)
    try:
        yield
    finally:
        current_invocation.reset(token)


_usage_sink: ContextVar[Callable[[int | None, int | None], object] | None] = ContextVar(
    "monitor_usage_sink", default=None
)


@contextmanager
def capture_usage(sink: Callable[[int | None, int | None], object]) -> Iterator[None]:
    lock = Lock()

    def write(input_tokens: int | None, output_tokens: int | None) -> None:
        with lock:
            sink(input_tokens, output_tokens)

    token = _usage_sink.set(write)
    try:
        yield
    finally:
        _usage_sink.reset(token)


def journal_usage(input_tokens: int | None, output_tokens: int | None) -> None:
    sink = _usage_sink.get()
    if sink is not None and (input_tokens is not None or output_tokens is not None):
        sink(input_tokens, output_tokens)


class ProfileReporter:
    def __init__(self, state_root: Path, run_id: str, profile: str) -> None:
        self.run_id = run_id
        self.profile = profile
        path = safe_owned_path(state_root, f"profile-events/{run_id}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes() if path.exists() else b""
        if data and not data.endswith(b"\n"):
            data = data[: data.rfind(b"\n") + 1]
            path.write_bytes(data)
        self.history = [MonitorEvent.model_validate_json(line) for line in data.splitlines()]
        self.sequence = len(self.history)
        self.generation = max((e.generation for e in self.history), default=-1) + 1
        self.output = path.open("a")

    def emit(
        self,
        phase: str,
        lifecycle: Literal["started", "completed", "failed", "cancelled", "interrupted", "usage"],
        execution_id: str,
        *,
        outcome: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_seconds: float | None = None,
        child_run_id: str | None = None,
    ) -> MonitorEvent:
        event = MonitorEvent(
            run_id=self.run_id,
            sequence=self.sequence + 1,
            timestamp=datetime.now(UTC).isoformat(),
            event_id=uuid4().hex,
            execution_id=execution_id,
            phase=phase,
            lifecycle=lifecycle,
            generation=self.generation,
            profile=self.profile,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_seconds=duration_seconds,
        )
        self.output.write(event.model_dump_json() + "\n")
        self.output.flush()
        os.fsync(self.output.fileno())
        self.sequence += 1
        return event

    def close(self) -> None:
        self.output.close()
