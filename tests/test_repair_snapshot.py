"""Direct-edit repair transaction integration tests."""
import os
import subprocess
from pathlib import Path

from refinery_ai.agents.repair_snapshot import RepairSnapshot


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def repository(root: Path) -> Path:
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'test@example.com')
    git(root, 'config', 'user.name', 'Test')
    (root / 'source').write_bytes(b'original\x00')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'initial')
    return root


def test_restore_preserves_pre_attempt_changes_index_links_and_modes(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / 'source').write_bytes(b'recipe edit')
    git(root, 'add', 'source')
    (root / 'source').write_bytes(b'accepted repair')
    (root / 'untracked').write_text('keep')
    (root / 'executable').write_text('run')
    (root / 'executable').chmod(0o755)
    (root / 'link').symlink_to('/does/not/exist')
    before = git(root, 'diff', '--cached', '--binary')
    snapshot = RepairSnapshot.capture(root)
    (root / 'source').unlink()
    (root / 'source').mkdir()
    (root / 'source' / 'nested').write_text('bad')
    (root / 'new').write_bytes(b'new\x00')
    (root / 'untracked').unlink()
    (root / 'executable').chmod(0o644)
    (root / 'link').unlink()
    (root / 'link').write_text('changed')
    git(root, 'add', '-A')
    assert snapshot.changed()
    assert snapshot.git_state_changed()
    snapshot.restore()
    assert (root / 'source').read_bytes() == b'accepted repair'
    assert (root / 'untracked').read_text() == 'keep'
    assert os.readlink(root / 'link') == '/does/not/exist'
    assert (root / 'executable').stat().st_mode & 0o777 == 0o755
    assert not (root / 'new').exists()
    assert git(root, 'diff', '--cached', '--binary') == before
    assert not snapshot.changed()
    snapshot.close()


def test_snapshot_excludes_generated_output_and_never_follows_links(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    root.mkdir()
    repository(root)
    outside = tmp_path / 'outside'
    outside.write_text('secret')
    (root / 'link').symlink_to(outside)
    (root / '.gitignore').write_text('ignored-source\n')
    (root / 'ignored-source').write_text('keep ignored')
    snapshot = RepairSnapshot.capture(root)
    (root / 'target').mkdir()
    (root / 'target' / 'generated').write_text('build')
    assert not snapshot.changed()
    (root / 'ignored-source').write_text('changed')
    (root / 'link').unlink()
    (root / 'link').mkdir()
    (root / 'link' / 'child').write_text('bad')
    snapshot.restore()
    assert outside.read_text() == 'secret'
    assert (root / 'link').is_symlink()
    assert (root / 'ignored-source').read_text() == 'keep ignored'
    snapshot.close()
