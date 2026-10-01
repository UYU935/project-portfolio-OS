import concurrent.futures
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
import pytest
from portfolio_os.approvals import approval_payload, create_approval, decide
from portfolio_os.domain import ProjectInput, RuleError, StaleState, digest
from portfolio_os.models import Approval, Evidence, Project, now
from conftest import add_project, evaluation

def proposal(store, policy, slug, action='HUMAN_ACTIVE', **kwargs):
    with store.tx() as s:
        p = s.scalar(select(Project).where(Project.slug == slug))
        a = create_approval(s, p, evaluation(action, **kwargs), policy)
        return a.id, a.digest

def accept(store, pair, **kwargs):
    return decide(store, *pair, approve=True, note='test-owner-approval', **kwargs)

def test_import_is_disabled_and_untriaged(store):
    pid = store.register(ProjectInput(slug='candidate', name='Candidate', summary='Needs owner confirmation'))
    p = store.project('candidate')
    assert p['id'] == pid
    assert (p['stage'], p['mode'], p['automation_enabled'], p['human_slot']) == ('INBOX', 'PARKED', False, None)

def test_import_never_overwrites_existing_state(store):
    p = add_project(store)
    store.register(ProjectInput(slug='alpha', name='MALICIOUS NEW NAME', summary='overwritten?'))
    assert store.project('alpha')['name'] == 'alpha'
    assert store.project('alpha')['revision'] == p['revision']

def test_human_cap_and_explicit_atomic_swap(store, policy):
    for slug in ('one','two','three','four'):
        add_project(store, slug)
    for slug in ('one','two','three'):
        accept(store, proposal(store, policy, slug))
    fourth = proposal(store, policy, 'four')
    with pytest.raises(RuleError, match='LIMIT = 3'):
        accept(store, fourth)
    assert store.project('four')['mode'] == 'AI'
    accept(store, fourth, replace_slug='two')
    data = store.export()
    assert sum(p['mode'] == 'HUMAN' for p in data['projects']) == 3
    assert store.project('two')['mode'] == 'AI'
    assert store.project('four')['mode'] == 'HUMAN'

def test_slot_constraint_rejects_four_even_without_application_code(store):
    add_project(store)
    with pytest.raises(IntegrityError):
        with store.tx() as s:
            p = s.scalar(select(Project))
            p.mode = 'HUMAN'; p.human_slot = 4

def test_slot_and_mode_must_agree(store):
    add_project(store)
    with pytest.raises(IntegrityError):
        with store.tx() as s:
            s.scalar(select(Project)).mode = 'HUMAN'

def test_concurrent_last_slot_has_one_winner(store, policy):
    for slug in ('one','two','three','four'):
        add_project(store, slug)
    for slug in ('one','two'):
        accept(store, proposal(store, policy, slug))
    candidates = [proposal(store, policy, slug) for slug in ('three','four')]
    def try_accept(pair):
        try:
            accept(store, pair)
            return 'ok'
        except RuleError:
            return 'blocked'
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(try_accept, candidates))
    assert sorted(outcomes) == ['blocked','ok']
    assert sum(p['mode'] == 'HUMAN' for p in store.export()['projects']) == 3

def test_digest_prevents_changed_proposal(store, policy):
    add_project(store)
    pair = proposal(store, policy, 'alpha')
    with store.tx() as s:
        s.get(Approval, pair[0]).reason = 'Changed after presentation'
    with pytest.raises(RuleError, match='digest'):
        accept(store, pair)
    assert store.project('alpha')['mode'] == 'AI'

def test_new_evidence_invalidates_pending_approval(store, policy):
    add_project(store)
    pair = proposal(store, policy, 'alpha')
    store.add_observation('alpha', 'Owner corrected an assumption')
    with pytest.raises(RuleError, match='STALE'):
        accept(store, pair)

def test_expiry_is_persisted_and_cannot_be_overridden(store, policy):
    add_project(store)
    pair = proposal(store, policy, 'alpha')
    with store.tx() as s:
        a = s.get(Approval, pair[0]); a.expires_at = now() - 1
        a.digest = digest(approval_payload(a)); pair = (a.id, a.digest)
    with pytest.raises(StaleState, match='expired'):
        accept(store, pair)
    with store.read() as s:
        assert s.get(Approval, pair[0]).status == 'EXPIRED'

def test_rejection_does_not_change_mode(store, policy):
    add_project(store)
    pair = proposal(store, policy, 'alpha')
    assert decide(store, *pair, approve=False, note='not now') == 'REJECTED'
    assert store.project('alpha')['mode'] == 'AI'

def test_duplicate_approval_cannot_be_replayed(store, policy):
    add_project(store)
    pair = proposal(store, policy, 'alpha')
    accept(store, pair)
    with pytest.raises(RuleError, match='not pending'):
        accept(store, pair)

def test_stage_cannot_skip(store, policy):
    add_project(store)
    with pytest.raises(RuleError, match='exactly one'):
        proposal(store, policy, 'alpha', 'ADVANCE', target_stage='PILOT')

def test_pilot_requires_human_and_real_owner_signal(store, policy):
    add_project(store)
    with store.tx() as s:
        s.scalar(select(Project)).stage = 'VALIDATE'
    pair = proposal(store, policy, 'alpha', 'ADVANCE', target_stage='PILOT')
    with pytest.raises(RuleError, match='customer evidence'):
        accept(store, pair)
    store.add_observation('alpha', 'Owner-entered customer observation', customer_signal=True)
    accept(store, proposal(store, policy, 'alpha'))
    accept(store, proposal(store, policy, 'alpha', 'ADVANCE', target_stage='PILOT'))
    assert store.project('alpha')['stage'] == 'PILOT'

def test_web_evidence_cannot_claim_customer_signal_in_db(store):
    p = add_project(store)
    with pytest.raises(IntegrityError):
        with store.tx() as s:
            s.add(Evidence(project_id=p['id'], statement='Blog article', title='Article',
                kind='WEB_RETRIEVED', customer_signal=True, fingerprint='f' * 64))

def test_archive_preserves_project_and_history(store, policy):
    p = add_project(store)
    accept(store, proposal(store, policy, 'alpha', 'ARCHIVE'))
    assert store.project('alpha')['mode'] == 'ARCHIVED'
    assert not store.project('alpha')['automation_enabled']
    assert store.project('alpha')['id'] == p['id']
    assert len(store.export()['decisions']) >= 4

def test_approval_queue_backpressure(store, policy):
    policy = policy.model_copy(update={'max_pending_approvals': 1})
    add_project(store, 'one'); add_project(store, 'two')
    proposal(store, policy, 'one')
    with store.tx() as s:
        p = s.scalar(select(Project).where(Project.slug == 'two'))
        assert create_approval(s, p, evaluation('HUMAN_ACTIVE'), policy) is None
