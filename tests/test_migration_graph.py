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
    from refinery_ai.monitor.journals import JournalCatalog
    from refinery_ai.telemetry import record_llm_usage

    store = setup_run(tmp_path)

    class Recipes:
        def run(self, snapshot, step, **kwargs):
            record_llm_usage({"input_tokens": 8, "output_tokens": 3})
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

    run = JournalCatalog(tmp_path, Path(".refinery")).list_runs()[0]
    assert (run.input_tokens, run.output_tokens) == (16, 6)
    assert any(event.get("lifecycle") == "usage" for event in custom)


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


def test_compiled_child_resumes_with_fresh_state_on_next_parent_visit(tmp_path: Path) -> None:
    from typing import TypedDict

    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    class ChildState(TypedDict, total=False):
        cycle: int
        visits: int
        migration_result: int

    class ParentState(TypedDict, total=False):
        cycle: int
        migration_result: int

    def build(saver):
        child = StateGraph(ChildState)
        child.add_node("prepare", lambda state: {"visits": state.get("visits", 0) + 1})

        def finish(state: ChildState) -> dict[str, int]:
            if state["cycle"] == 1:
                interrupt("resume migration")
            return {"migration_result": state["visits"]}

        child.add_node("finish", finish)
        child.add_edge(START, "prepare")
        child.add_edge("prepare", "finish")
        child.add_edge("finish", END)
        parent = StateGraph(ParentState)
        parent.add_node("migrate", child.compile())

        def advance(state: ParentState) -> dict[str, int]:
            assert state["migration_result"] == 1
            return {"cycle": state["cycle"] + 1}

        parent.add_node("advance", advance)
        parent.add_edge(START, "migrate")
        parent.add_conditional_edges(
            "migrate", lambda state: state["cycle"] == 1, {True: "advance", False: END}
        )
        parent.add_edge("advance", "migrate")
        return parent.compile(checkpointer=saver)

    database = str(tmp_path / "nested.sqlite")
    config = {"configurable": {"thread_id": "profile"}}
    with SqliteSaver.from_conn_string(database) as saver:
        interrupted = build(saver).invoke({"cycle": 1}, config, durability="sync")
        assert interrupted["__interrupt__"][0].value == "resume migration"

    with SqliteSaver.from_conn_string(database) as saver:
        completed = build(saver).invoke(Command(resume=True), config, durability="sync")
        assert completed == {"cycle": 2, "migration_result": 1}


def test_profile_topology_exposes_compiled_migration_phases() -> None:
    from refinery_ai.workflows.profile import build_profile_graph

    graph = build_profile_graph(None).get_graph(xray=True)
    assert "migrate:execute_recipe" in graph.nodes
    assert any(
        edge.source == "migrate:repair" and edge.target == "migrate:verify_candidate"
        for edge in graph.edges
    )
