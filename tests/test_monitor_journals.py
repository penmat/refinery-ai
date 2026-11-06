import json
from pathlib import Path

import pytest
from test_migration_events import state

from refinery_ai.migrations.events import MigrationReporter
from refinery_ai.monitor.journals import JournalCatalog, JournalTail


def journal(root):
    path = root / ".refinery/migration-events" / ("a" * 32 + ".jsonl")
    reporter = MigrationReporter(path, None)
    reporter.emit("repair", "started", state(root), "one")
    reporter.close()
    return path


def test_partial_append_and_replay_have_identical_sequences(tmp_path):
    path = journal(tmp_path)
    data = json.loads(path.read_text())
    tail = JournalTail(path, "a" * 32)
    assert [e.sequence for e in tail.read_batch()] == [1]
    data.update(sequence=2, lifecycle="completed", event_id="two")
    encoded = (json.dumps(data, ensure_ascii=False) + "\n").encode()
    with path.open("ab") as output:
        output.write(encoded[:-3])
    assert tail.read_batch() == []
    with path.open("ab") as output:
        output.write(encoded[-3:])
    assert [e.sequence for e in tail.read_batch()] == [2]
    snapshot = JournalCatalog(tmp_path, Path(".refinery")).snapshot("a" * 32)
    assert snapshot.cursor == 2
    assert [e.sequence for e in snapshot.events] == [1, 2]
    assert snapshot.topology.historical is False


@pytest.mark.parametrize(
    "bad", [b"{}\n", b"{bad}\n", b"x" * (1024 * 1024 + 1)], ids=["empty", "invalid", "oversized"]
)
def test_corrupt_record_is_a_diagnostic_and_healthy_runs_remain(tmp_path, bad):
    path = journal(tmp_path)
    with path.open("ab") as output:
        output.write(bad)
    catalog = JournalCatalog(tmp_path, Path(".refinery"))
    assert catalog.list_runs()[0].diagnostics
    assert catalog.snapshot("a" * 32).cursor == 1


def test_tail_detects_replacement_and_truncation(tmp_path):
    path = journal(tmp_path)
    tail = JournalTail(path, "a" * 32)
    tail.read_batch()
    path.write_text("")
    with pytest.raises(ValueError, match="changed"):
        tail.read_batch()


def test_catalog_rejects_traversal_and_symlinked_journals(tmp_path):
    path = journal(tmp_path)
    catalog = JournalCatalog(tmp_path, Path(".refinery"))
    with pytest.raises(ValueError):
        catalog.snapshot("../outside")
    content = path.read_bytes()
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(content)
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        catalog.snapshot("a" * 32)


def test_unsupported_version_sequence_and_identity_rejected(tmp_path):
    path = journal(tmp_path)
    data = json.loads(path.read_text())
    for change in [dict(schema_version=2), dict(sequence=3), dict(run_id="b" * 32)]:
        path.write_text(json.dumps({**data, **change}) + "\n")
        assert JournalCatalog(tmp_path, Path(".refinery")).snapshot("a" * 32).diagnostics


def test_empty_catalog_and_partial_utf8(tmp_path):
    assert JournalCatalog(tmp_path, Path(".refinery")).list_runs() == []
    path = journal(tmp_path)
    with path.open("ab") as output:
        output.write(b'{"recipe":"\xc3')
    assert JournalCatalog(tmp_path, Path(".refinery")).snapshot("a" * 32).cursor == 1


def test_tail_batch_limit(tmp_path):
    path = journal(tmp_path)
    data = json.loads(path.read_text())
    with path.open("a") as output:
        for seq in range(2, 301):
            output.write(json.dumps({**data, "sequence": seq}) + "\n")
    tail = JournalTail(path, "a" * 32)
    assert len(tail.read_batch()) == 256
    assert len(tail.read_batch()) == 44


def test_legacy_terminal_without_domain_outcome_is_unknown(tmp_path):
    path = journal(tmp_path)
    data = json.loads(path.read_text())
    phases = ["record_step_failure", "publish_result", "finish_run"]
    path.write_text(
        "".join(
            json.dumps(
                {
                    **data,
                    "sequence": i + 1,
                    "phase": phase,
                    "lifecycle": "completed",
                    "outcome": None,
                }
            )
            + "\n"
            for i, phase in enumerate(phases)
        )
    )
    assert JournalCatalog(tmp_path, Path(".refinery")).snapshot("a" * 32).run.status == "unknown"


def test_replaced_state_root_cannot_expose_external_topology(tmp_path):
    journal(tmp_path)
    catalog = JournalCatalog(tmp_path, Path(".refinery"))
    state_root = tmp_path / ".refinery"
    state_root.rename(tmp_path / "original")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "migration-events").mkdir()
    (outside / "migration-events" / ("a" * 32 + ".jsonl")).write_bytes(
        (tmp_path / "original/migration-events" / ("a" * 32 + ".jsonl")).read_bytes()
    )
    state_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match=r"root|symlink"):
        catalog.snapshot("a" * 32)


def test_unsafe_event_directory_does_not_hide_healthy_runs(tmp_path):
    journal(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".refinery/profile-events").symlink_to(outside, target_is_directory=True)
    catalog = JournalCatalog(tmp_path, Path(".refinery"))
    assert [run.run_id for run in catalog.list_runs()] == ["a" * 32]
    assert catalog.discovery_diagnostics


def test_recipe_summary_deduplicates_recovered_steps(tmp_path):
    path = journal(tmp_path)
    data = json.loads(path.read_text())
    path.write_text(
        "".join(
            json.dumps(
                {
                    **data,
                    "sequence": i + 1,
                    "phase": "checkpoint_step",
                    "lifecycle": "completed",
                    "step": step,
                    "generation": i,
                }
            )
            + "\n"
            for i, step in enumerate([1, 1, 2])
        )
    )
    assert JournalCatalog(tmp_path, Path(".refinery")).snapshot("a" * 32).run.verified_recipes == 2
