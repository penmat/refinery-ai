import subprocess
import sys
from pathlib import Path

import pytest

from refinery_ai.migrations.process_safety import MigrationLease, spawn_owned, stop_group


def test_second_owner_is_refused(tmp_path: Path) -> None:
    with MigrationLease(tmp_path, Path('.refinery'), 'first'):
        with pytest.raises(RuntimeError, match='active'):
            with MigrationLease(tmp_path, Path('.refinery'), 'second'):
                pass


def test_live_group_blocks_recovery_after_owner_releases(tmp_path: Path) -> None:
    with MigrationLease(tmp_path, Path('.refinery'), 'first'):
        child = spawn_owned([sys.executable, '-c', 'import time; time.sleep(30)'], cwd=tmp_path)
        # Synchronize on the bootstrap's durable launch acknowledgement.
        registry = tmp_path / '.refinery/processes/first'
        import time
        deadline = time.monotonic() + 5
        while not list(registry.glob('*.json')) and time.monotonic() < deadline:
            time.sleep(.01)
        assert list(registry.glob('*.json'))
    try:
        with pytest.raises(RuntimeError, match='active'):
            with MigrationLease(tmp_path, Path('.refinery'), 'first'):
                pass
    finally:
        stop_group(child)
    with MigrationLease(tmp_path, Path('.refinery'), 'first'):
        pass


def test_unbound_process_keeps_original_command_behavior(tmp_path: Path) -> None:
    process = spawn_owned([sys.executable, '-c', "print('ok')"], cwd=tmp_path)
    assert process.communicate(timeout=5)[0].strip() == b'ok'
