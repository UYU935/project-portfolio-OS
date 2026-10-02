from portfolio_os.approval_pilot import complete_review_pilot, reserve_review_pilot
from portfolio_os.domain import Policy, ProjectInput
from portfolio_os.models import Approval, Decision, Run
from portfolio_os.provider import Result
from conftest import evaluation


def register_with_evidence(store, slug="approval-pilot"):
    store.register(ProjectInput(
        slug=slug,
        name="Approval Pilot",
        summary="Sanitized synthetic business concept.",
        domain="media",
    ))
    store.add_observation(slug, "A synthetic owner observation for test coverage.")
    return store.project(slug)


def test_human_active_creates_pending_approval_without_project_change(store):
    project = register_with_evidence(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    evidence_id = snapshot["evidence"][0]["id"]
    before = store.project("approval-pilot")

    run_id = reserve_review_pilot(store, "approval-pilot", snapshot, policy, "model")
    outcome = complete_review_pilot(
        store,
        run_id,
        snapshot,
        Result(evaluation("HUMAN_ACTIVE", evidence_ids=[evidence_id])),
        policy,
    )

    after = store.project("approval-pilot")
    assert outcome["approval_created"] is True
    assert outcome["approval_action"] == "HUMAN_ACTIVE"
    for key in ("stage", "mode", "automation_enabled", "human_slot", "revision"):
        assert after[key] == before[key]

    with store.read() as session:
        approval = session.query(Approval).filter(Approval.project_id == project["id"]).one()
        assert approval.status == "PENDING"
        run = session.get(Run, run_id)
        assert run.status == "SUCCEEDED"
        assert run.output["applied_to_project"] is False
        events = [d.event for d in session.query(Decision).filter(Decision.project_id == project["id"]).all()]
        assert "PILOT_APPROVAL_REVIEW_RESERVED" in events
        assert "APPROVAL_REQUESTED" in events
        assert "PILOT_APPROVAL_REVIEW_RECORDED" in events


def test_hold_records_review_without_approval(store):
    project = register_with_evidence(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    before = store.project("approval-pilot")

    run_id = reserve_review_pilot(store, "approval-pilot", snapshot, policy, "model")
    outcome = complete_review_pilot(
        store,
        run_id,
        snapshot,
        Result(evaluation("HOLD")),
        policy,
    )

    after = store.project("approval-pilot")
    assert outcome["approval_created"] is False
    assert outcome["effective_recommendation"] == "HOLD"
    for key in ("stage", "mode", "automation_enabled", "human_slot", "revision"):
        assert after[key] == before[key]
    with store.read() as session:
        assert session.query(Approval).count() == 0


def test_redundant_park_on_parked_project_does_not_create_approval(store):
    project = register_with_evidence(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)

    run_id = reserve_review_pilot(store, "approval-pilot", snapshot, policy, "model")
    outcome = complete_review_pilot(
        store,
        run_id,
        snapshot,
        Result(evaluation("PARK")),
        policy,
    )

    assert outcome["approval_created"] is False
    assert outcome["effective_recommendation"] == "HOLD"


def test_unknown_evidence_id_fails_closed(store):
    project = register_with_evidence(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    run_id = reserve_review_pilot(store, "approval-pilot", snapshot, policy, "model")
    try:
        complete_review_pilot(
            store,
            run_id,
            snapshot,
            Result(evaluation("HUMAN_ACTIVE", evidence_ids=["invented"])),
            policy,
        )
    except Exception as exc:
        assert "unknown_evidence" in str(exc)
    else:
        raise AssertionError("Unknown evidence IDs must be rejected")
