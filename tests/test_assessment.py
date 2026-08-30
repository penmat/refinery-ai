import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from refinery_ai.assessment.comparison import compare
from refinery_ai.assessment.engine import AssessmentEngine
from refinery_ai.assessment.models import Category, ComparisonCompatibility
from refinery_ai.assessment.storage import AssessmentStore
from refinery_ai.cli import app

FIXTURES = Path(__file__).parent / "fixtures"


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "legacy-java"
    shutil.copytree(FIXTURES / "legacy-java", repository)
    return repository


def _stable_payload(repository: Path) -> dict[str, object]:
    assessment = AssessmentEngine().assess(repository)
    payload = assessment.model_dump(mode="json")
    for key in ("assessment_id", "created_at"):
        payload.pop(key)
    payload["repository"].pop("root")  # type: ignore[union-attr]
    for analyzer in payload["analyzers"].values():  # type: ignore[union-attr]
        analyzer.pop("duration_seconds")
    return payload


def test_assessment_is_stable_and_leaves_unmeasured_categories_unscored(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    first = _stable_payload(repository)
    second = _stable_payload(repository)

    assert first == second
    assert first["score"]["categories"][Category.DEPENDENCIES] is None  # type: ignore[index]
    assert first["completeness"] == 0.4


def test_storage_is_immutable_and_names_resolve(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    assessment = AssessmentEngine().assess(repository, name="baseline")
    store = AssessmentStore(repository)

    path = store.save(assessment)

    assert path.name == f"{assessment.assessment_id}.json"
    assert store.load("baseline").assessment_id == assessment.assessment_id
    assert store.list()[0].assessment_id == assessment.assessment_id
    assert (
        json.loads((path.parent / "names.json").read_text())["baseline"] == assessment.assessment_id
    )


def test_compatible_comparison_reports_metric_and_score_changes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    previous = AssessmentEngine().assess(repository)
    java_file = repository / "src/main/java/example/App.java"
    java_file.write_text(java_file.read_text() + "\n" * 600)
    current = AssessmentEngine().assess(repository)

    result = compare(previous, current)

    assert result.compatibility is ComparisonCompatibility.COMPATIBLE
    assert result.overall_change is not None and result.overall_change < 0
    assert any(item.metric == "source.productionLines" for item in result.metric_changes)


def test_incompatible_profiles_suppress_score_deltas(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    previous = AssessmentEngine().assess(repository)
    current = AssessmentEngine().assess(repository)
    current.profile.fingerprint = "sha256:different"

    result = compare(previous, current)

    assert result.compatibility is ComparisonCompatibility.PARTIALLY_COMPATIBLE
    assert result.overall_change is None
    assert all(value is None for value in result.category_changes.values())


def test_assess_cli_persists_and_compares_by_name(
    tmp_path: Path, stub_codegraph: None
) -> None:
    repository = _repository(tmp_path)
    runner = CliRunner()

    baseline = runner.invoke(
        app,
        [
            "analyze",
            str(repository),
            "--name",
            "baseline",
            "--no-sonarqube",
            "--no-dependencies",
        ],
    )
    comparison = runner.invoke(
        app,
        [
            "analyze",
            str(repository),
            "--compare",
            "baseline",
            "--no-sonarqube",
            "--no-dependencies",
        ],
    )

    assert baseline.exit_code == 0
    assert "Modernization posture" in baseline.stdout
    assert comparison.exit_code == 0
    latest = repository / ".refinery/assessment.md"
    assert "Compatibility: compatible" in latest.read_text()
    comparison_reports = list((repository / ".refinery/assessments").glob("*.comparison.md"))
    assert len(comparison_reports) == 1
    assert comparison_reports[0].read_text() == latest.read_text()


def test_assess_cli_retains_text_and_json_stdout(
    tmp_path: Path, stub_codegraph: None
) -> None:
    repository = _repository(tmp_path)
    runner = CliRunner()

    text_result = runner.invoke(
        app, ["analyze", str(repository), "--no-sonarqube", "--no-dependencies"]
    )
    json_result = runner.invoke(
        app,
        ["analyze", str(repository), "--json", "--no-sonarqube", "--no-dependencies"],
    )

    assert text_result.exit_code == 0
    assert json_result.exit_code == 0
    assert (repository / ".refinery/summary.md").read_text() == text_result.stdout
    assert (repository / ".refinery/summary.json").read_text() == json_result.stdout
    assessment = json.loads((repository / ".refinery/assessment.json").read_text())
    assessment_id = assessment["assessment_id"]
    historical = repository / f".refinery/assessments/{assessment_id}.report.json"
    assert json.loads(historical.read_text()) == assessment
