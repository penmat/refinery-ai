"""Explicit migration workflow, shared by standalone and nested profile execution."""
from __future__ import annotations

import contextlib
import os
import time
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from refinery_ai.migrations.events import MigrationReporter
from refinery_ai.migrations import baseline, step_execution, repair_execution
from refinery_ai.migrations.execution_context import MigrationExecutionContext
from refinery_ai.migrations.execution_state import MigrationExecutionState, MigrationRequest, RunManifest, RecoveryPoint
from refinery_ai.migrations.process_safety import MigrationLease
from refinery_ai.migrations.run_store import MigrationRunStore
from refinery_ai.models import PersistedMigrationPlan, MigrationRun


class MigrationGraphState(TypedDict, total=False):
    data: MigrationExecutionState
    result: MigrationRun


def initial_migration_state(plan: PersistedMigrationPlan, request: MigrationRequest, manifest: RunManifest | None,
                            recovery: RecoveryPoint | None = None) -> MigrationGraphState:
    state = MigrationExecutionState(plan=plan,run_id=manifest.run_id if manifest else uuid4().hex,
        apply=request.apply,publish=manifest.publish if manifest else request.publish)
    if recovery is not None and manifest is not None:
        state.generation = recovery.generation
        state.index = recovery.next_step
        state.results = recovery.results
        state.starting_revision = manifest.starting_revision
        state.current_revision = recovery.revision
        state.current_java = recovery.java
        if recovery.baseline is not None:
            state.baseline_verification = recovery.baseline.verification
            state.baseline_artifacts = recovery.baseline.artifacts
    return {'data':state}


def build_migration_graph(context: MigrationExecutionContext | None, *, checkpointer: Any = None) -> Any:
    graph = StateGraph(MigrationGraphState)
    operations = {
        'prepare_run':baseline.prepare_run,'verify_baseline':baseline.verify_baseline,
        'prepare_step':step_execution.prepare_step,'execute_recipe':step_execution.execute_recipe,
        'verify_candidate':step_execution.verify_candidate,'select_repair':repair_execution.select_repair,
        'repair':repair_execution.repair,'evaluate_repair':repair_execution.evaluate_repair,
        'checkpoint_step':step_execution.checkpoint_step,'record_step_failure':step_execution.record_step_failure,
        'publish_result':step_execution.publish_result,
    }
    def node(name: str, operation: Any) -> Any:
        def invoke(state: MigrationGraphState) -> MigrationGraphState:
            assert context is not None
            value = state['data'].model_copy(deep=True)
            before = value.model_copy(deep=True)
            execution_id = uuid4().hex
            started = time.monotonic()
            stage = context.stages[value.stage_index].name if value.stage_index<len(context.stages) and value.attempt else None
            reporter = context.reporter
            if reporter is not None:
                reporter.emit(name,'started',before,execution_id,stage=stage)
            try:
                value.outcome = None
                result = operation(context,value)
                if reporter is not None:
                    reporter.emit(name,'completed',before,execution_id,duration=time.monotonic()-started,
                                  outcome=value.outcome,checkpoint=value.current_revision,stage=stage)
                return {'data':value, **({'result':result} if isinstance(result,MigrationRun) else {})}
            except BaseException as error:
                if reporter is not None:
                    with contextlib.suppress(Exception):
                        reporter.emit(name,'cancelled' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'failed',
                                      before,execution_id,duration=time.monotonic()-started,stage=stage)
                raise
        return invoke
    for name, operation in operations.items():
        graph.add_node(name,node(name,operation))
    def recover(state: MigrationGraphState) -> MigrationGraphState:
        assert context is not None
        from refinery_ai.migrations.worktrees import GitWorktreeManager
        value = state['data'].model_copy(deep=True)
        context.worktrees = GitWorktreeManager(context.runner.store.repository,context.runner.store.state_directory,value.run_id,value.plan.plan_id)
        return {'data':value}
    graph.add_node('recover_run',node('recover_run',lambda ctx,value: recover({'data':value})))
    graph.add_node('finish_run',node('finish_run',step_execution.finish_run))
    graph.add_conditional_edges(START,lambda s:'recover_run' if s['data'].generation else 'prepare_run', ['prepare_run','recover_run'])
    graph.add_conditional_edges('prepare_run',lambda s:'verify_baseline' if s['data'].apply else 'prepare_step' if s['data'].plan.steps else 'publish_result', ['verify_baseline','prepare_step','publish_result'])
    graph.add_conditional_edges('recover_run',lambda s:'verify_baseline' if s['data'].baseline_verification is None else 'prepare_step' if s['data'].index<=len(s['data'].plan.steps) else 'publish_result',['verify_baseline','prepare_step','publish_result'])
    def after_baseline(s: MigrationGraphState) -> str:
        return 'prepare_step' if s['data'].status=='completed' and s['data'].index<=len(s['data'].plan.steps) else 'publish_result'
    graph.add_conditional_edges('verify_baseline',after_baseline,['prepare_step','publish_result'])
    graph.add_conditional_edges('prepare_step',lambda s:'record_step_failure' if s['data'].failure_message else 'execute_recipe',['record_step_failure','execute_recipe'])
    graph.add_conditional_edges('execute_recipe',lambda s:'record_step_failure' if s['data'].failure_reason else 'verify_candidate' if s['data'].apply else 'checkpoint_step',['record_step_failure','verify_candidate','checkpoint_step'])
    def after_verify(s: MigrationGraphState) -> str:
        value=s['data']
        if value.failure_reason:
            return 'record_step_failure'
        if value.pending:
            return 'evaluate_repair'
        return 'checkpoint_step' if value.build is not None and value.build.green else 'select_repair'
    graph.add_conditional_edges('verify_candidate',after_verify,['record_step_failure','evaluate_repair','checkpoint_step','select_repair'])
    graph.add_conditional_edges('select_repair',lambda s:'record_step_failure' if s['data'].failure_reason else 'repair',['record_step_failure','repair'])
    graph.add_conditional_edges('repair',lambda s:'verify_candidate' if s['data'].pending else 'select_repair',['verify_candidate','select_repair'])
    graph.add_conditional_edges('evaluate_repair',lambda s:'checkpoint_step' if s['data'].build is not None and s['data'].build.green else 'select_repair',['checkpoint_step','select_repair'])
    graph.add_conditional_edges('checkpoint_step',lambda s:'prepare_step' if s['data'].index<=len(s['data'].plan.steps) else 'publish_result',['prepare_step','publish_result'])
    graph.add_edge('record_step_failure','publish_result')
    graph.add_edge('publish_result','finish_run')
    graph.add_edge('finish_run',END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else False)


def run_migration_graph(runner: Any, request: MigrationRequest) -> MigrationRun:
    plan = runner.store.load(request.plan_id)
    if request.expected_profile is not None and plan.profile != request.expected_profile:
        raise ValueError(f'Migration plan uses {plan.profile or "no profile"}; requested profile is {request.expected_profile}')
    initial = initial_migration_state(plan,request,None)
    run_id=initial['data'].run_id
    store=MigrationRunStore(runner.store.repository,runner.store.state_directory,run_id) if request.apply else None
    ctx=MigrationExecutionContext(runner,request,store)
    with contextlib.ExitStack() as resources:
        if request.apply and os.name=='posix':
            resources.enter_context(MigrationLease(runner.store.repository,runner.store.state_directory,run_id))
        checkpointer=None
        if store is not None:
            checkpointer=resources.enter_context(SqliteSaver.from_conn_string(str(store.checkpoints)))
        ctx.reporter=MigrationReporter(store.journal if store is not None else None,runner.event_observer)
        graph=build_migration_graph(ctx,checkpointer=checkpointer)
        budgets=sum(max(0,stage.max_attempts) for stage in ctx.stages)
        config={'configurable':{'thread_id':f'{run_id}:0'},'recursion_limit':20+len(plan.steps)*(10+5*budgets)}
        try:
            result=graph.invoke(initial,config=config,**({'durability':'sync'} if store is not None else {}))
            return MigrationRun.model_validate(result['result'])
        finally:
            try:
                ctx.rollback_pending()
            finally:
                ctx.reporter.close()
