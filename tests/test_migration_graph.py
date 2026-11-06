from pathlib import Path

from refinery_ai.workflows.migration import build_migration_graph


def test_migration_graph_has_explicit_phases_and_repair_edges() -> None:
    graph = build_migration_graph(None).get_graph()
    assert {'prepare_run', 'recover_run', 'verify_baseline', 'prepare_step', 'execute_recipe',
            'verify_candidate', 'select_repair', 'repair', 'evaluate_repair',
            'checkpoint_step', 'record_step_failure', 'publish_result', 'finish_run'} <= set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ('repair', 'verify_candidate') in edges
    assert ('evaluate_repair', 'select_repair') in edges
