import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_monitor_journals import journal

from refinery_ai.monitor.journals import JournalCatalog
from refinery_ai.monitor.server import create_app, stream_events


def test_api_snapshot_and_origin_confinement(tmp_path):
    journal(tmp_path)
    app = create_app(tmp_path, Path(".refinery"), allowed_host="127.0.0.1:8765")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert len(client.get("/api/runs").json()) == 1
        snap = client.get("/api/runs/" + "a" * 32).json()
        assert snap["cursor"] == 1
        assert client.get("/api/runs/bad").status_code == 400
        assert client.get("/api/runs/" + "b" * 32).status_code == 404
        assert (
            client.get("/api/runs", headers={"origin": "https://evil.example"}).status_code == 403
        )
        assert client.get("/api/runs", headers={"host": "evil.example"}).status_code == 403
        assert client.get("/api/missing").status_code == 404


class Connection:
    async def is_disconnected(self):
        return False


@pytest.mark.asyncio
async def test_snapshot_race_and_reconnect_replay(tmp_path):
    path = journal(tmp_path)
    catalog = JournalCatalog(tmp_path, Path(".refinery"))
    cursor = catalog.snapshot("a" * 32).cursor
    event = json.loads(path.read_text())
    with path.open("a") as output:
        output.write(json.dumps({**event, "sequence": 2, "event_id": "two"}) + "\n")
    stream = stream_events(Connection(), catalog, "a" * 32, cursor)
    chunk = await anext(stream)
    assert "id: 2\n" in chunk
    await stream.aclose()
    with path.open("a") as output:
        output.write(json.dumps({**event, "sequence": 3, "event_id": "three"}) + "\n")
    stream = stream_events(Connection(), catalog, "a" * 32, 2)
    chunk = await anext(stream)
    assert "id: 3\n" in chunk
    await stream.aclose()
    assert catalog.snapshot("a" * 32).cursor == 3


@pytest.mark.asyncio
async def test_stream_reports_corruption_without_modifying_journal(tmp_path):
    path = journal(tmp_path)
    with path.open("ab") as output:
        output.write(b"bad\n")
    before = path.read_bytes()
    stream = stream_events(Connection(), JournalCatalog(tmp_path, Path(".refinery")), "a" * 32, 1)
    chunk = await anext(stream)
    assert "event: diagnostic" in chunk
    await stream.aclose()
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_disconnect_releases_stream_without_worker_changes(tmp_path):
    path = journal(tmp_path)

    class Gone:
        async def is_disconnected(self):
            return True

    stream = stream_events(Gone(), JournalCatalog(tmp_path, Path(".refinery")), "a" * 32, 0)
    assert [part async for part in stream] == []
    assert path.exists()
