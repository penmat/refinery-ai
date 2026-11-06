from pathlib import Path

import pytest

from refinery_ai.migrations.events import MigrationReporter, read_migration_events
from refinery_ai.migrations.execution_state import MigrationExecutionState
from refinery_ai.models import BuildSystem, DiscoverySnapshot, PersistedMigrationPlan


def state(root: Path) -> MigrationExecutionState:
    return MigrationExecutionState(plan=PersistedMigrationPlan(plan_id='plan',snapshot=DiscoverySnapshot(repository=root,build_system=BuildSystem.MAVEN),steps=[]),run_id='a'*32,apply=True,publish=True)


def test_journal_replays_sequence_and_repairs_only_partial_tail(tmp_path: Path) -> None:
    path=tmp_path/'events.jsonl'
    reporter=MigrationReporter(path,None)
    reporter.emit('repair','started',state(tmp_path),'execution')
    reporter.close()
    with path.open('ab') as output:
        output.write(b'{"incomplete":')
    reporter=MigrationReporter(path,None)
    reporter.emit('repair','interrupted',state(tmp_path),'execution')
    reporter.close()
    events=read_migration_events(path)
    assert [event.sequence for event in events]==[1,2]
    assert [event.lifecycle for event in events]==['started','interrupted']
    assert events[0].execution_id==events[1].execution_id


def test_observer_failure_does_not_prevent_journal(tmp_path: Path) -> None:
    def bad(event):
        raise RuntimeError('observer unavailable')
    path=tmp_path/'events.jsonl'
    reporter=MigrationReporter(path,bad)
    reporter.emit('repair','completed',state(tmp_path),'one',outcome='accepted')
    reporter.close()
    assert read_migration_events(path)[0].outcome=='accepted'
