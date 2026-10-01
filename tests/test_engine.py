import asyncio
from sqlalchemy import select
import pytest
from portfolio_os.domain import Busy, BudgetExhausted, Evaluation, Finding, Research, RuleError, StaleState
from portfolio_os.engine import apply_evaluation, claim_task, persist_research, tick, work_one
from portfolio_os.models import Approval, Control, Project, Run, Task, now
from portfolio_os.provider import DemoProvider, ProviderError, Result
from conftest import add_project, evaluation

class FixedProvider:
    model = 'test-fixture'
    def __init__(self, action='HOLD', failure=None, research=None):
        self.action = action; self.failure = failure; self.calls = 0; self.research_value = research
    async def evaluate(self, snapshot, policy):
        self.calls += 1
        if self.failure: raise self.failure
        return Result(evaluation(self.action))
    async def research(self, snapshot, task, policy):
        self.calls += 1
        if self.failure: raise self.failure
        return Result(self.research_value or Research(summary='No new external evidence',findings=[],counterargument='No demonstrated demand',unanswered=['Unknown demand']),
                      sources=[f.url for f in self.research_value.findings] if self.research_value else [])

def start(store, policy):
    store.pause(False)
    return store.acquire(policy)

def test_paused_tick_makes_no_provider_calls(store, policy, root):
    add_project(store)
    provider = FixedProvider()
    with pytest.raises(RuleError, match='paused'):
        asyncio.run(tick(store, provider, root, policy))
    assert provider.calls == 0
    assert not store.export()['runs']

def test_only_enrolled_projects_are_due(store):
    from portfolio_os.domain import ProjectInput
    store.register(ProjectInput(slug='disabled', name='Disabled', summary='Not approved for AI'))
    assert store.due(3) == []

def test_review_research_and_persistence_are_real_state_transitions(store, policy, root):
    add_project(store)
    store.pause(False)
    out = asyncio.run(tick(store, FixedProvider('RESEARCH'), root, policy))
    assert out['reviews'][0]['action'] == 'RESEARCH'
    assert out['tasks'] == ['RESEARCH_DONE:new_sources=0']
    data = store.export()
    assert len(data['runs']) == 2
    assert all(r['status'] == 'SUCCEEDED' for r in data['runs'])
    assert data['tasks'][0]['status'] == 'DONE'
    assert not data['evidence']

def test_codex_brief_created_but_codex_not_executed(store, policy, root):
    add_project(store, domain='software')
    store.pause(False)
    asyncio.run(tick(store, FixedProvider('CODEX_BRIEF'), root, policy))
    t = store.export()['tasks'][0]
    assert t['status'] == 'DONE'
    contents = (root / t['artifact']).read_text()
    assert '未実行' in contents and '第1段階' in contents
    assert len(store.export()['runs']) == 1

def test_daily_reservations_include_failures(store, policy):
    p = add_project(store)
    token = start(store, policy)
    snap = store.snapshot(p['id'], policy)
    for _ in range(policy.max_paid_requests_per_day):
        rid = store.reserve(p['id'], 'REVIEW', snap, policy, token, 'fixture')
        store.fail_run(rid, 'test_failure')
    with pytest.raises(BudgetExhausted):
        store.reserve(p['id'], 'REVIEW', snap, policy, token, 'fixture')

def test_budget_exhausted_research_remains_queued(store, policy, root):
    policy = policy.model_copy(update={'max_paid_requests_per_day': 1})
    add_project(store); store.pause(False)
    result = asyncio.run(tick(store, FixedProvider('RESEARCH'), root, policy))
    assert result['tasks'] == ['BUDGET_LIMIT']
    assert store.export()['tasks'][0]['status'] == 'QUEUED'

def test_same_evaluation_does_not_duplicate_task(store, policy):
    p = add_project(store); token = start(store, policy)
    snap = store.snapshot(p['id'], policy)
    for _ in range(2):
        rid = store.reserve(p['id'], 'REVIEW', snap, policy, token, 'fixture')
        apply_evaluation(store, token, snap, rid, Result(evaluation('RESEARCH')), policy)
    assert len(store.export()['tasks']) == 1

def test_fabricated_evidence_ids_fail_closed(store, policy):
    p = add_project(store); token = start(store, policy)
    snap = store.snapshot(p['id'], policy)
    rid = store.reserve(p['id'], 'REVIEW', snap, policy, token, 'fixture')
    with pytest.raises(RuleError, match='evidence IDs'):
        apply_evaluation(store, token, snap, rid, Result(evaluation('RESEARCH', evidence_ids=['invented'])), policy)
    assert not store.export()['tasks']

def test_revision_change_during_model_call_blocks_result(store, policy):
    p = add_project(store); token = start(store, policy)
    snap = store.snapshot(p['id'], policy)
    rid = store.reserve(p['id'], 'REVIEW', snap, policy, token, 'fixture')
    store.add_observation('alpha', 'A new fact arrived')
    with pytest.raises(StaleState):
        apply_evaluation(store, token, snap, rid, Result(evaluation('PARK')), policy)
    assert store.project('alpha')['mode'] == 'AI'

def test_pause_during_model_call_prevents_state_mutation(store, policy):
    p = add_project(store); token = start(store, policy)
    snap = store.snapshot(p['id'], policy)
    rid = store.reserve(p['id'], 'REVIEW', snap, policy, token, 'fixture')
    store.pause(True)
    with pytest.raises(RuleError, match='paused'):
        apply_evaluation(store, token, snap, rid, Result(evaluation('PARK')), policy)
    assert store.project('alpha')['mode'] == 'AI'

def test_second_worker_is_rejected(store, policy):
    start(store, policy)
    with pytest.raises(Busy):
        store.acquire(policy)

def test_expired_lease_needs_explicit_recovery(store, policy):
    token = start(store, policy)
    with store.tx() as s:
        s.get(Control,1).lease_until = now() - 1
    with pytest.raises(Busy, match='recover'):
        store.acquire(policy)
    store.recover()
    assert store.acquire(policy) != token

def test_unknown_completion_not_silently_retried(store, policy, root):
    add_project(store); store.pause(False)
    provider = FixedProvider(failure=ProviderError('timeout', uncertain=True))
    asyncio.run(tick(store, provider, root, policy))
    assert provider.calls == 1
    assert store.export()['runs'][0]['status'] == 'UNCERTAIN'
    assert store.due(3) == []

def test_ai_park_is_reversible_not_delete(store, policy, root):
    add_project(store); store.pause(False)
    asyncio.run(tick(store, FixedProvider('PARK'), root, policy))
    assert store.project('alpha')['mode'] == 'PARKED'
    assert store.project('alpha')['automation_enabled']
    assert not store.export()['approvals']
    store.enroll('alpha')
    assert store.project('alpha')['mode'] == 'AI'

def test_human_park_always_requests_approval(store, policy, root):
    add_project(store)
    with store.tx() as s:
        p = s.scalar(select(Project)); p.mode='HUMAN'; p.human_slot=1
    store.pause(False)
    asyncio.run(tick(store, FixedProvider('PARK'), root, policy))
    assert store.project('alpha')['mode'] == 'HUMAN'
    assert store.export()['approvals'][0]['status'] == 'PENDING'

def test_no_new_evidence_parks_after_threshold(store, policy, root):
    add_project(store)
    with store.tx() as s:
        s.scalar(select(Project)).idle_reviews=2
    store.pause(False)
    asyncio.run(tick(store, FixedProvider('RESEARCH'), root, policy))
    assert store.project('alpha')['mode'] == 'PARKED'
    assert not store.export()['tasks']

def test_new_web_sources_not_customer_evidence(store, policy, root):
    add_project(store); store.pause(False)
    research = Research(summary='Source available', findings=[Finding(statement='A public specification exists',
        url='https://example.com/spec',title='Specification',limitation='Not customer demand')],
        counterargument='No willingness to pay demonstrated',unanswered=['Demand'])
    asyncio.run(tick(store, FixedProvider('RESEARCH', research=research), root, policy))
    e = store.export()['evidence'][0]
    assert e['customer_signal'] is False
    assert store.due(3)

def test_approval_backlog_prevents_more_review_spending(store, policy, root):
    policy=policy.model_copy(update={'max_pending_approvals':1})
    from test_state_and_approvals import proposal
    add_project(store,'one'); add_project(store,'two')
    proposal(store,policy,'one')
    store.pause(False); provider=FixedProvider()
    result=asyncio.run(tick(store,provider,root,policy))
    assert provider.calls == 0
    assert result['approval_backpressure']

def test_codex_brief_not_repeated_without_material_change(store, policy, root):
    add_project(store, domain='software'); store.pause(False)
    asyncio.run(tick(store, FixedProvider('CODEX_BRIEF'), root, policy))
    with store.tx() as s:
        s.scalar(select(Project)).next_review_at = now() - 1
    asyncio.run(tick(store, FixedProvider('CODEX_BRIEF'), root, policy))
    assert len(store.export()['tasks']) == 1

def test_no_redundant_human_promotion_approval(store, policy, root):
    add_project(store)
    with store.tx() as s:
        p=s.scalar(select(Project));p.mode='HUMAN';p.human_slot=1
    store.pause(False)
    asyncio.run(tick(store, FixedProvider('HUMAN_ACTIVE'), root, policy))
    assert not store.export()['approvals']
