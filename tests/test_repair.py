from refinery_ai.models import VerificationResult
from refinery_ai.repair import RepairBudget


def failure(message: str) -> VerificationResult:
    return VerificationResult(
        command=["mvn", "test"], exit_code=1, stderr=message, duration_seconds=0.1
    )


def test_stops_repeated_failure() -> None:
    budget = RepairBudget(3)
    assert budget.allow(failure("compiler error"))
    assert not budget.allow(failure("compiler error"))


def test_stops_at_attempt_limit() -> None:
    budget = RepairBudget(1)
    assert budget.allow(failure("first"))
    assert not budget.allow(failure("second"))
