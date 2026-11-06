import sys
from pathlib import Path

import pytest

from refinery_ai.migrations.process_safety import MigrationLease, spawn_owned, stop_group


def test_second_owner_is_refused(tmp_path: Path) -> None:
    with (
        MigrationLease(tmp_path, Path(".refinery"), "first"),
        pytest.raises(RuntimeError, match="active"),
        MigrationLease(tmp_path, Path(".other-state"), "second"),
    ):
        pass


def test_live_group_blocks_recovery_after_owner_releases(tmp_path: Path) -> None:
    with MigrationLease(tmp_path, Path(".refinery"), "first") as lease:
        child = spawn_owned([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
        # Synchronize on the bootstrap's durable launch acknowledgement.
        registry = lease.records
        import time

        deadline = time.monotonic() + 5
        while not list(registry.glob("*.json")) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert list(registry.glob("*.json"))
    try:
        with (
            pytest.raises(RuntimeError, match="active"),
            MigrationLease(tmp_path, Path(".refinery"), "first"),
        ):
            pass
    finally:
        stop_group(child)
    with MigrationLease(tmp_path, Path(".refinery"), "first"):
        pass


def test_unbound_process_keeps_original_command_behavior(tmp_path: Path) -> None:
    process = spawn_owned([sys.executable, "-c", "print('ok')"], cwd=tmp_path)
    assert process.communicate(timeout=5)[0].strip() == b"ok"


def test_child_that_closes_lease_descriptor_still_blocks_recovery(tmp_path: Path) -> None:
    import time

    ready = tmp_path / "ready"
    script = (
        "import os,time\nfrom pathlib import Path\n"
        "for fd in range(3,256):\n"
        "    try: os.close(fd)\n"
        "    except OSError: pass\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(30)\n"
    )
    with MigrationLease(tmp_path, Path(".refinery"), "first"):
        child = spawn_owned([sys.executable, "-c", script], cwd=tmp_path)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
    try:
        with (
            pytest.raises(RuntimeError, match="process group"),
            MigrationLease(tmp_path, Path(".refinery"), "first"),
        ):
            pass
    finally:
        stop_group(child)


def test_worktree_ancestor_symlink_is_refused(tmp_path: Path) -> None:
    from refinery_ai.migrations.worktrees import GitWorktreeManager

    state = tmp_path / ".refinery"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "worktrees").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        GitWorktreeManager(tmp_path, Path(".refinery"), "run", "plan")


def test_git_commands_register_owned_processes(tmp_path: Path) -> None:
    from refinery_ai.migrations.worktrees import GitWorktreeManager

    with MigrationLease(tmp_path, Path(".refinery"), "git") as lease:
        GitWorktreeManager._git(tmp_path, "init", "-q")
        assert list(lease.records.glob("*.json"))
