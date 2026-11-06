"""One-attempt repair operations; routing and budgets are explicit graph state."""
from refinery_ai.migrations.execution_context import MigrationExecutionContext
from refinery_ai.migrations.execution_state import MigrationExecutionState
from refinery_ai.migrations.deterministic_repair import springdoc_repair_applicable
from refinery_ai.migrations.repair import TransactionalMigrationRepairer
from refinery_ai.migrations.step_execution import label
from refinery_ai.telemetry import observe_llm


def select_repair(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    assert state.build is not None and state.workspace is not None
    while state.stage_index < len(ctx.stages):
        stage = ctx.stages[state.stage_index]
        if (state.attempt >= stage.max_attempts or (not stage.uses_model and not springdoc_repair_applicable(state.workspace.path,state.build))):
            state.stage_index += 1
            state.attempt = 0
        else:
            state.attempt += 1
            return
    state.failure_reason = 'Post-migration build failed'
    state.failure_message = f'Post-migration build failed: {state.plan.steps[state.index-1].recipe}'


def repair(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    stage = ctx.stages[state.stage_index]
    assert state.workspace is not None and state.build is not None
    ctx.progress(f'Attempting {stage.name} repair {state.attempt}/{stage.max_attempts}: {label(state)}')
    if stage.uses_model:
        with observe_llm('repair',run_id=state.run_id,plan_id=state.plan.plan_id,repair_attempt=state.attempt):
            result = stage.repairer.repair(state.workspace.path,state.plan.steps[state.index-1],state.build)
    else:
        result = stage.repairer.repair(state.workspace.path,state.plan.steps[state.index-1],state.build)
    result = result.model_copy(update={'command':['refinery',f'{stage.name.lower()}-repair']})
    state.repairs.append(result)
    state.outcome = 'applied' if result.green else 'rejected'
    if not result.green:
        ctx.progress(f'{stage.name.capitalize()} repair attempt {state.attempt} did not apply: '+(result.stderr.strip() or 'no diagnostic was returned'))
        if not (stage.uses_model and isinstance(stage.repairer,TransactionalMigrationRepairer)):
            state.stage_index += 1
            state.attempt = 0
        return
    state.pending = True
    if stage.uses_model and isinstance(stage.repairer,TransactionalMigrationRepairer):
        ctx.pending = stage.repairer


def evaluate_repair(ctx: MigrationExecutionContext, state: MigrationExecutionState) -> None:
    from refinery_ai.migrations.runner import _failure_score
    assert state.candidate is not None and state.build is not None
    stage = ctx.stages[state.stage_index]
    if ctx.pending is not None:
        if state.candidate.green or _failure_score(state.candidate) < _failure_score(state.build):
            state.outcome = 'accepted'
            ctx.pending.accept_last()
            state.build = state.candidate
            ctx.pending = None
        else:
            state.outcome = 'rolled_back'
            ctx.rollback_pending()
            ctx.progress(f'Rolled back {stage.name} repair {state.attempt}: verification did not improve')
    else:
        state.build = state.candidate
    state.pending = False
    state.candidate = None
