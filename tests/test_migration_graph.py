from pathlib import Path

from refinery_ai.workflows.migration import build_migration_graph


def test_migration_graph_has_explicit_phases_and_repair_edges() -> None:
    graph = build_migration_graph(None).get_graph()
    assert {
        "prepare_run",
        "recover_run",
        "verify_baseline",
        "prepare_step",
        "execute_recipe",
        "verify_candidate",
        "select_repair",
        "repair",
        "evaluate_repair",
        "checkpoint_step",
        "record_step_failure",
        "publish_result",
        "finish_run",
    } <= set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("repair", "verify_candidate") in edges
    assert ("evaluate_repair", "select_repair") in edges


def test_real_parent_stream_exposes_nested_migration_events(tmp_path: Path) -> None:
    from langgraph.graph import END, START, StateGraph
    from test_migration_recovery import setup_run

    from refinery_ai.migrations.runner import MigrationRunner
    from refinery_ai.models import ExecutionType, VerificationResult

    store = setup_run(tmp_path)

    class Recipes:
        def run(self, snapshot, step, **kwargs):
            return VerificationResult(command=["recipe"], exit_code=0, duration_seconds=0)

    runner = MigrationRunner(
        store,
        runners={ExecutionType.OPENREWRITE: Recipes()},
        build_verifier=lambda *args, **kwargs: VerificationResult(
            command=["test"], exit_code=0, duration_seconds=0
        ),
    )
    graph = StateGraph(dict)
    graph.add_node("migrate", lambda state: {"result": runner.run(apply=True)})
    graph.add_edge(START, "migrate")
    graph.add_edge("migrate", END)
    chunks = list(graph.compile().stream({}, stream_mode=["tasks", "custom"], subgraphs=True))
    custom = [chunk[-1] for chunk in chunks if chunk[-2] == "custom"]
    assert any(
        event.get("phase") == "execute_recipe" and event.get("lifecycle") == "completed"
        for event in custom
    )


def test_long_preview_has_no_durable_side_effects(tmp_path: Path) -> None:
    from test_migration_recovery import setup_run

    from refinery_ai.migrations.runner import MigrationRunner
    from refinery_ai.models import ExecutionType, VerificationResult

    store = setup_run(tmp_path)
    plan = store.load()
    plan.steps = [plan.steps[0].model_copy(update={"recipe": f"recipe{i}"}) for i in range(40)]
    (store.root / "plan1.json").write_text(plan.model_dump_json())

    class Recipes:
        def run(self, *args, **kwargs):
            return VerificationResult(command=["preview"], exit_code=0, duration_seconds=0)

    result = MigrationRunner(store, runners={ExecutionType.OPENREWRITE: Recipes()}).run()
    assert len(result.steps) == 40
    assert not (tmp_path / ".refinery/migration-checkpoints.sqlite").exists()
    assert not (tmp_path / ".refinery/migration-events").exists()
