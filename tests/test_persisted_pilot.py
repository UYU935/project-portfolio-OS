from portfolio_os.domain import Policy, ProjectInput
from portfolio_os.models import Decision, Project, Run
from portfolio_os.pilot import complete_pilot, fail_pilot, reserve_pilot
from portfolio_os.provider import ProviderError, Result
from conftest import evaluation


def register_parked(store, slug="pilot"):
    store.register(ProjectInput(
        slug=slug,
        name="Pilot",
        summary="Sanitized synthetic business brief.",
        domain="media",
    ))
    project = store.project(slug)
    return project


def test_persisted_pilot_records_result_without_project_mutation(store):
    project = register_parked(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    before = store.project("pilot")

    run_id = reserve_pilot(store, "pilot", snapshot, policy, "model")
    complete_pilot(
        store,
        run_id,
        snapshot,
        Result(evaluation(), input_tokens=100, output_tokens=20, request_id="req-test"),
    )

    after = store.project("pilot")
    for key in ("revision", "stage", "mode", "automation_enabled", "human_slot"):
        assert after[key] == before[key]

    with store.read() as session:
        run = session.get(Run, run_id)
        assert run.status == "SUCCEEDED"
        assert run.output["pilot_record_only"] is True
        assert run.output["applied_to_project"] is False
        assert run.output["result"]["recommendation"] == "HOLD"
        assert run.input_tokens == 100 and run.output_tokens == 20
        events = [d.event for d in session.query(Decision).filter(Decision.project_id == project["id"]).all()]
        assert "PILOT_EVALUATION_RESERVED" in events
        assert "PILOT_EVALUATION_RECORDED" in events


def test_failed_pilot_is_closed_without_retry(store):
    project = register_parked(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    run_id = reserve_pilot(store, "pilot", snapshot, policy, "model")
    fail_pilot(store, run_id, ProviderError("OPENAI_RATE_LIMITED"))

    with store.read() as session:
        run = session.get(Run, run_id)
        assert run.status == "FAILED"
        assert run.error_code == "OPENAI_RATE_LIMITED"


def test_uncertain_pilot_stays_uncertain_for_owner_review(store):
    project = register_parked(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    run_id = reserve_pilot(store, "pilot", snapshot, policy, "model")
    fail_pilot(store, run_id, ProviderError("NETWORK_COMPLETION_UNKNOWN", uncertain=True))

    with store.read() as session:
        run = session.get(Run, run_id)
        assert run.status == "UNCERTAIN"
        assert run.error_code == "NETWORK_COMPLETION_UNKNOWN"
