from pathlib import Path

import pytest

from refinery_ai.assessment.models import AnalyzerResult
from refinery_ai.discovery import discover
from refinery_ai.inspectors import (
    DiscoveryInspector,
    InspectionRequest,
    InspectionRunner,
    InspectorRegistry,
)
from refinery_ai.models import DiscoverySnapshot


class RecordingInspector:
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[InspectionRequest] = []

    def inspect(self, request: InspectionRequest) -> str:
        self.requests.append(request)
        return request.repository.name


def test_registry_preserves_registration_and_selection_order() -> None:
    first = RecordingInspector()
    second = RecordingInspector()
    second.name = "second"
    registry = InspectorRegistry[str]([first, second])

    assert registry.names == ("recording", "second")
    assert tuple(item.name for item in registry.select(["second", "recording"])) == (
        "second",
        "recording",
    )


def test_registry_rejects_duplicate_and_unknown_names() -> None:
    inspector = RecordingInspector()

    with pytest.raises(ValueError, match="already registered"):
        InspectorRegistry[str]([inspector, inspector])
    with pytest.raises(KeyError, match="Unknown inspector"):
        InspectorRegistry[str]([inspector]).select(["missing"])


def test_runner_discovers_once_and_shares_request(tmp_path: Path) -> None:
    calls: list[Path] = []
    first = RecordingInspector()
    second = RecordingInspector()
    second.name = "second"

    def recorded_discovery(repository: Path) -> DiscoverySnapshot:
        calls.append(repository)
        return discover(repository)

    bundle = InspectionRunner(
        InspectorRegistry[str]([first, second]), discoverer=recorded_discovery
    ).inspect(tmp_path)

    assert calls == [tmp_path.resolve()]
    assert bundle.results == {"recording": tmp_path.name, "second": tmp_path.name}
    assert first.requests[0] is second.requests[0]


def test_local_inspector_returns_assessment_evidence(tmp_path: Path) -> None:
    snapshot = discover(tmp_path)
    request = InspectionRequest(repository=tmp_path.resolve(), snapshot=snapshot)

    result = DiscoveryInspector().inspect(request)

    assert isinstance(result, AnalyzerResult)
    assert result.analyzer == "discovery"
