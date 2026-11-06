"""Exclusive migration ownership and persistent child process-group tracking."""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

_active: ContextVar[MigrationLease | None] = ContextVar('migration_lease', default=None)


def stop_group(process: subprocess.Popen[bytes]) -> None:
    if os.name != 'posix':
        if process.poll() is None:
            process.kill()
        process.wait()
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=.5)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


class MigrationLease:
    def __init__(self, repository: Path, state_directory: Path, run_id: str) -> None:
        self.root = repository.resolve() / state_directory
        self.records = self.root / 'processes' / run_id
        self.descriptor: int | None = None
        self.token: Token[MigrationLease | None] | None = None

    def __enter__(self) -> MigrationLease:
        if os.name != 'posix':
            raise RuntimeError('Safe migration ownership/resume requires POSIX support')
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True)
        self.descriptor = os.open(self.root / 'migration.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_children_stopped()
        except BaseException as error:
            os.close(self.descriptor)
            self.descriptor = None
            if isinstance(error, BlockingIOError):
                raise RuntimeError('Another migration worker is active') from error
            raise
        self.records.mkdir(parents=True, exist_ok=True)
        self.token = _active.set(self)
        return self

    def assert_children_stopped(self) -> None:
        # Check all prior runs in this checkout, not just the run being resumed.
        for record in (self.root / 'processes').glob('*/*.json'):
            group = json.loads(record.read_text())['group']
            if not isinstance(group, int) or group <= 1:
                raise RuntimeError(f'Invalid owned process record: {record}')
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                pass
            raise RuntimeError(f'Owned process group {group} may still be active; refusing recovery')

    def __exit__(self, *arguments: Any) -> None:
        if self.token is not None:
            _active.reset(self.token)
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def spawn_owned(
    command: list[str], *, cwd: Path, stdin: BinaryIO | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    lease = _active.get()
    if lease is not None:
        assert lease.descriptor is not None
        record = lease.records / f'{uuid4().hex}.json'
        command = [sys.executable, str(Path(__file__).with_name('process_guard.py')),
                   str(record), str(lease.descriptor), *command]
    return subprocess.Popen(
        command, cwd=cwd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, start_new_session=os.name == 'posix',
        pass_fds=(lease.descriptor,) if lease is not None and lease.descriptor is not None else (),
    )


def run_owned(
    command: list[str], *, cwd: Path, timeout: int, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = spawn_owned(command, cwd=cwd, env=env)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode,
                                           stdout.decode(errors='replace'), stderr.decode(errors='replace'))
    finally:
        stop_group(process)


def ownership_active() -> bool:
    return _active.get() is not None
