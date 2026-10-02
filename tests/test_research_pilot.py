from portfolio_os.domain import Finding, Policy, ProjectInput, Research
from portfolio_os.models import Decision, Evidence, Run
from portfolio_os.provider import ProviderError, Result
from portfolio_os.research_pilot import (
    complete_research_pilot,
    fail_research_pilot,
    reserve_research_pilot,
)


def register_parked(store, slug="research-pilot"):
    store.register(ProjectInput(
        slug=slug,
        name="Research Pilot",
        summary="Sanitized synthetic business concept.",
        domain="media",
    ))
    return store.project(slug)


def research_result(url="https://example.com/source"):
    value = Research(
        summary="Synthetic source-backed research.",
        findings=[
            Finding(
                statement="A public source contains a relevant market signal.",
                url=url,
                title="Public source",
                limitation="This is public-web evidence, not verified customer demand.",
            )
        ],
        counterargument="The signal may not translate into willingness to pay.",
        unanswered=["Customer demand remains unverified."],
    )
    return Result(
        value,
        input_tokens=120,
        output_tokens=30,
        request_id="req-research-test",
        sources=[url],
    )


def test_research_pilot_persists_evidence_but_not_operating_state(store):
    project = register_parked(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    before = store.project("research-pilot")

    run_id = reserve_research_pilot(store, "research-pilot", snapshot, policy, "model")
    added = complete_research_pilot(store, run_id, snapshot, research_result())

    after = store.project("research-pilot")
    assert added == 1
    assert after["revision"] == before["revision"] + 1
    for key in ("stage", "mode", "automation_enabled", "human_slot"):
        assert after[key] == before[key]

    with store.read() as session:
        run = session.get(Run, run_id)
        assert run.status == "SUCCEEDED"
        assert run.output["pilot_research_only"] is True
        assert run.output["new_evidence_count"] == 1
        evidence = session.query(Evidence).filter(Evidence.run_id == run_id).one()
        assert evidence.kind == "WEB_RETRIEVED"
        assert evidence.customer_signal is False
        events = [d.event for d in session.query(Decision).filter(Decision.project_id == project["id"]).all()]
        assert "PILOT_RESEARCH_RESERVED" in events
        assert "PILOT_RESEARCH_RECORDED" in events


def test_research_pilot_deduplicates_same_url(store):
    project = register_parked(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    first = reserve_research_pilot(store, "research-pilot", snapshot, policy, "model")
    assert complete_research_pilot(store, first, snapshot, research_result()) == 1

    refreshed = store.snapshot(project["id"], policy)
    second = reserve_research_pilot(store, "research-pilot", refreshed, policy, "model")
    assert complete_research_pilot(store, second, refreshed, research_result()) == 0


def test_research_pilot_rejects_unretrieved_url(store):
    project = register_parked(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    run_id = reserve_research_pilot(store, "research-pilot", snapshot, policy, "model")
    result = research_result()
    result.sources = ["https://example.com/different"]
    try:
        complete_research_pilot(store, run_id, snapshot, result)
    except Exception as exc:
        assert "unsupported_evidence_provenance" in str(exc)
    else:
        raise AssertionError("Unsupported source should be rejected")


def test_failed_research_pilot_is_closed(store):
    project = register_parked(store)
    policy = Policy()
    snapshot = store.snapshot(project["id"], policy)
    run_id = reserve_research_pilot(store, "research-pilot", snapshot, policy, "model")
    fail_research_pilot(store, run_id, ProviderError("OPENAI_RATE_LIMITED"))
    with store.read() as session:
        run = session.get(Run, run_id)
        assert run.status == "FAILED"
        assert run.error_code == "OPENAI_RATE_LIMITED"
