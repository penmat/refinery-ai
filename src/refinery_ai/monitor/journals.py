"""Confined, incremental readers for independently written run journals."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from refinery_ai.migrations.run_store import safe_owned_path, safe_state_root
from refinery_ai.monitor.models import GraphTopology, MonitorEvent, RunSnapshot, RunSummary
from refinery_ai.monitor.topology import current_topology

MAX_LINE = 1024 * 1024


def validate_run_id(run_id: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid run ID")


class JournalTail:
    """Read only newline-committed records, in bounded batches."""

    def __init__(self, path: Path, run_id: str) -> None:
        validate_run_id(run_id)
        self.path = path
        self.run_id = run_id
        self.offset = 0
        self.sequence = 0
        self.identity: tuple[int, int] | None = None
        self.previous_size = 0
        self.error: ValueError | None = None

    def read_batch(self, limit: int = 256) -> list[MonitorEvent]:
        if self.error is not None:
            raise self.error
        if limit < 1 or limit > 256:
            raise ValueError("Batch size must be between 1 and 256")
        if self.path.is_symlink() or any(p.is_symlink() for p in self.path.parents):
            raise ValueError("Journal path must not contain symlinks")
        descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        events: list[MonitorEvent] = []
        with os.fdopen(descriptor, "rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if self.identity is not None and (
                identity != self.identity or stat.st_size < self.previous_size
            ):
                raise ValueError("Journal changed: reload the run")
            self.identity = identity
            self.previous_size = stat.st_size
            stream.seek(self.offset)
            for _ in range(limit):
                line = stream.readline(MAX_LINE + 1)
                try:
                    if len(line) > MAX_LINE:
                        raise ValueError("Journal record exceeds 1 MiB")
                    if not line.endswith(b"\n"):
                        break
                    try:
                        event = MonitorEvent.model_validate_json(line)
                    except ValidationError as error:
                        raise ValueError("Malformed record or unsupported event schema") from error
                    if event.run_id != self.run_id or event.sequence != self.sequence + 1:
                        raise ValueError("Invalid journal identity or sequence")
                except ValueError as error:
                    if not events:
                        raise
                    self.error = error
                    break
                self.sequence = event.sequence
                self.offset = stream.tell()
                events.append(event)
        return events


class JournalCatalog:
    def __init__(self, repository: Path, state_directory: Path) -> None:
        self.state_root = safe_state_root(repository, state_directory)

    def journal(self, run_id: str) -> tuple[Path, Literal["profile", "migration"]]:
        validate_run_id(run_id)
        matches: list[tuple[Path, Literal["profile", "migration"]]] = []
        for kind in ("profile", "migration"):
            path = safe_owned_path(self.state_root, f"{kind}-events/{run_id}.jsonl")
            if path.is_file():
                matches.append((path, kind))
        if len(matches) != 1:
            raise FileNotFoundError("Run not found or ambiguous")
        return matches[0]

    def list_runs(self) -> list[RunSummary]:
        ids: set[str] = set()
        for kind in ("profile", "migration"):
            directory = safe_owned_path(self.state_root, f"{kind}-events")
            if directory.is_dir():
                ids.update(
                    p.stem
                    for p in directory.glob("*.jsonl")
                    if re.fullmatch(r"[a-f0-9]{32}", p.stem)
                )
        results: list[RunSummary] = []
        for run_id in ids:
            try:
                results.append(self.snapshot(run_id).run)
            except (OSError, ValueError):
                results.append(
                    RunSummary(
                        run_id=run_id,
                        kind="migration",
                        diagnostics=["Run journal is unavailable or unsafe"],
                    )
                )
        return sorted(results, key=lambda r: r.last_seen or "", reverse=True)

    def snapshot(self, run_id: str) -> RunSnapshot:
        path, kind = self.journal(run_id)
        tail = JournalTail(path, run_id)
        events: list[MonitorEvent] = []
        diagnostics: list[str] = []
        while True:
            try:
                batch = tail.read_batch()
            except (OSError, ValueError) as error:
                diagnostics.append(str(error))
                break
            events.extend(batch)
            if len(batch) < 256 and tail.error is None:
                break
        metadata = safe_owned_path(self.state_root, f"monitor-topologies/{run_id}.json")
        topology = current_topology(kind)
        if metadata.exists():
            try:
                candidate = GraphTopology.model_validate_json(metadata.read_bytes())
                if candidate.kind != kind:
                    raise ValueError("Topology kind does not match run")
                topology = candidate
            except (OSError, ValueError):
                diagnostics.append("Recorded topology unavailable; showing current definition")
        first = events[0] if events else None
        last = events[-1] if events else None
        terminal = next(
            (
                e
                for e in reversed(events)
                if e.phase in ("run", "finish_run") and e.lifecycle == "completed"
            ),
            None,
        )
        status = terminal.outcome or "completed" if terminal else "unknown"
        if terminal is None and last is not None and last.lifecycle in ("failed", "cancelled"):
            status = last.lifecycle
        summary = RunSummary(
            run_id=run_id,
            kind=kind,
            profile=first.profile if first else None,
            parent_run_id=first.parent_run_id if first else None,
            parent_execution_id=first.parent_execution_id if first else None,
            first_seen=first.timestamp if first else None,
            last_seen=last.timestamp if last else None,
            event_count=len(events),
            status=status,
            diagnostics=diagnostics,
        )
        return RunSnapshot(
            run=summary,
            events=events,
            topology=topology,
            cursor=events[-1].sequence if events else 0,
            diagnostics=diagnostics,
        )
