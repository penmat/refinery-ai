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
        self.root_identity: tuple[int, int] | None = None
        self.discovery_diagnostics: list[str] = []
        self._validate_root()

    def _validate_root(self) -> None:
        if self.state_root.resolve() != self.state_root or self.state_root.is_symlink():
            raise ValueError("State root or ancestor changed to a symlink")
        if self.state_root.exists():
            stat = self.state_root.stat()
            identity = (stat.st_dev, stat.st_ino)
            if self.root_identity is not None and identity != self.root_identity:
                raise ValueError("State root changed; restart the monitor")
            self.root_identity = identity

    def _owned(self, relative: str) -> Path:
        self._validate_root()
        return safe_owned_path(self.state_root, relative)

    def _metadata(self, run_id: str) -> bytes:
        relative = f"monitor-topologies/{run_id}.json"
        path = self._owned(relative)
        if os.name != "posix":
            return path.read_bytes()
        # Anchor every component to a checked directory descriptor so a rename
        # between validation and reading cannot redirect the metadata read.
        root_fd = os.open(self.state_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            stat = os.fstat(root_fd)
            if (stat.st_dev, stat.st_ino) != self.root_identity:
                raise ValueError("State root changed")
            directory_fd = os.open(
                "monitor-topologies", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
            )
            try:
                file_fd = os.open(
                    f"{run_id}.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
                )
                with os.fdopen(file_fd, "rb") as stream:
                    data = stream.read(MAX_LINE + 1)
                    if len(data) > MAX_LINE:
                        raise ValueError("Topology metadata exceeds 1 MiB")
                    return data
            finally:
                os.close(directory_fd)
        finally:
            os.close(root_fd)

    def journal(self, run_id: str) -> tuple[Path, Literal["profile", "migration"]]:
        validate_run_id(run_id)
        self._validate_root()
        errors: list[ValueError] = []
        matches: list[tuple[Path, Literal["profile", "migration"]]] = []
        for kind in ("profile", "migration"):
            try:
                path = self._owned(f"{kind}-events/{run_id}.jsonl")
                if path.is_file():
                    matches.append((path, kind))
            except ValueError as error:
                errors.append(error)
        if not matches and errors:
            raise ValueError("Run journal or directory is an unsafe symlink") from errors[0]
        if len(matches) != 1:
            raise FileNotFoundError("Run not found or ambiguous")
        return matches[0]

    def list_runs(self) -> list[RunSummary]:
        ids: set[str] = set()
        self.discovery_diagnostics = []
        for kind in ("profile", "migration"):
            try:
                directory = self._owned(f"{kind}-events")
                if directory.is_dir():
                    ids.update(
                        p.stem
                        for p in directory.glob("*.jsonl")
                        if re.fullmatch(r"[a-f0-9]{32}", p.stem)
                    )
            except (OSError, ValueError):
                self.discovery_diagnostics.append(
                    f"Cannot read {kind} journal directory; unsafe or unavailable"
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
        topology = current_topology(kind)
        try:
            candidate = GraphTopology.model_validate_json(self._metadata(run_id))
            if candidate.kind != kind:
                raise ValueError("Topology kind does not match run")
            topology = candidate
        except FileNotFoundError:
            pass  # Legacy journal without saved topology.
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
        status = (terminal.outcome or "unknown") if terminal else "unknown"
        if terminal is None and last is not None and last.lifecycle in ("failed", "cancelled"):
            status = last.lifecycle
        inputs = [e.input_tokens for e in events if e.input_tokens is not None]
        outputs = [e.output_tokens for e in events if e.output_tokens is not None]
        current_step = next(
            (
                e
                for e in reversed(events)
                if e.migration_name
                and e.step is not None
                and e.phase not in ("prepare_run", "recover_run", "verify_baseline")
            ),
            None,
        )
        summary = RunSummary(
            migration_name=current_step.migration_name if current_step else None,
            step=current_step.step if current_step else None,
            total_steps=next(
                (e.total_steps for e in reversed(events) if e.total_steps is not None), None
            ),
            input_tokens=sum(inputs) if inputs else None,
            output_tokens=sum(outputs) if outputs else None,
            run_id=run_id,
            kind=kind,
            profile=first.profile if first else None,
            parent_run_id=first.parent_run_id if first else None,
            parent_execution_id=first.parent_execution_id if first else None,
            first_seen=first.timestamp if first else None,
            last_seen=last.timestamp if last else None,
            event_count=len(events),
            status=status,
            verified_recipes=len(
                {
                    e.step
                    for e in events
                    if e.phase == "checkpoint_step"
                    and e.lifecycle == "completed"
                    and e.step is not None
                }
            ),
            diagnostics=diagnostics,
        )
        return RunSnapshot(
            run=summary,
            events=events,
            topology=topology,
            cursor=events[-1].sequence if events else 0,
            diagnostics=diagnostics,
        )
