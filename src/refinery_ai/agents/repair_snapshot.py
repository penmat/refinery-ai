"""Disk-backed source transactions for agents that edit a worktree directly."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

EXCLUDED = {".git", "target", "build", ".gradle", ".refinery", ".codegraph", "graphify-out"}


def _entries(root: Path) -> dict[Path, tuple[int, str]]:
    entries: dict[Path, tuple[int, str]] = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name not in EXCLUDED]
        for name in [*directories, *files]:
            if name in EXCLUDED:
                continue
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                value = os.readlink(path)
            elif stat.S_ISREG(mode):
                with path.open("rb") as source:
                    value = hashlib.file_digest(source, "sha256").hexdigest()
            elif stat.S_ISDIR(mode):
                value = ""
            else:
                raise ValueError(f"Unsupported source file type: {path}")
            entries[path.relative_to(root)] = (mode, value)
    return entries


class RepairSnapshot:
    """Preserve source bytes, links, modes and Git state before one attempt."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()
        self.storage = tempfile.TemporaryDirectory(prefix="refinery-repair-")
        self.backup = Path(self.storage.name)
        self.entries = _entries(self.repository)
        for relative, (mode, _) in self.entries.items():
            source, target = self.repository / relative, self.backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                target.symlink_to(os.readlink(source))
            elif stat.S_ISDIR(mode):
                target.mkdir(exist_ok=True)
            else:
                shutil.copy2(source, target)
        self.head_path = Path(
            self._git("rev-parse", "--path-format=absolute", "--git-path", "HEAD")
        )
        self.index_path = Path(
            self._git("rev-parse", "--path-format=absolute", "--git-path", "index")
        )
        self.head = self.head_path.read_bytes()
        self.revision = self._git("rev-parse", "HEAD")
        self.index = self.index_path.read_bytes() if self.index_path.exists() else None
        self.staged = self._git("ls-files", "--stage", "-z")

    @classmethod
    def capture(cls, repository: Path) -> RepairSnapshot:
        return cls(repository)

    def _git(self, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.repository), *arguments], text=True
        ).strip()

    def git_state_changed(self) -> bool:
        return (
            self.head_path.read_bytes() != self.head
            or self._git("rev-parse", "HEAD") != self.revision
            or self._git("ls-files", "--stage", "-z") != self.staged
        )

    def changed(self) -> bool:
        return _entries(self.repository) != self.entries

    def restore(self) -> None:
        current = _entries(self.repository)
        # Remove children first, without traversing a link at any level.
        for relative in sorted(current, key=lambda path: len(path.parts), reverse=True):
            path = self.repository / relative
            mode = current[relative][0]
            previous = self.entries.get(relative)
            if stat.S_ISDIR(mode):
                if previous is None or not stat.S_ISDIR(previous[0]):
                    shutil.rmtree(path)
            elif previous != current[relative]:
                path.unlink()
        for relative, (mode, _) in sorted(
            self.entries.items(), key=lambda item: len(item[0].parts)
        ):
            path = self.repository / relative
            if stat.S_ISDIR(mode):
                path.mkdir(exist_ok=True)
                path.chmod(stat.S_IMODE(mode))
            elif not path.exists() and not path.is_symlink():
                if stat.S_ISLNK(mode):
                    path.symlink_to(os.readlink(self.backup / relative))
                else:
                    shutil.copy2(self.backup / relative, path)
        # HEAD may be symbolic in callers other than migration's detached worktrees.
        if self.head.startswith(b"ref: "):
            ref = self.head.decode().strip().removeprefix("ref: ")
            self._git("update-ref", ref, self.revision)
        self.head_path.write_bytes(self.head)
        if self.index is None:
            self.index_path.unlink(missing_ok=True)
        else:
            self.index_path.write_bytes(self.index)

    def close(self) -> None:
        self.storage.cleanup()
