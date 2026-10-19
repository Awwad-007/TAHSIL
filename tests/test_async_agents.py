import asyncio
from datetime import datetime

from app import llm_orchestrator as orch
from app.guardrail_engine import evaluate
from app.schemas import AgentResult, InterventionType, LLMProposal, RecoveryState
from app.state_store import StateStore
from app.vector_index import seed_history


def _state() -> RecoveryState:
    return RecoveryState(
        invoice_id="inv_async_001",
        customer_id="cust_async_001",
        retry_count=1,
        last_attempt_ts=datetime(2026, 9, 4, 8, 0),
        predicted_liquidity_window_start=datetime(2026, 9, 5, 8, 0),
        predicted_liquidity_window_end=datetime(2026, 9, 5, 14, 0),
    )


def test_three_agents_run_concurrently_and_merge():
    async def run():
        gate = asyncio.Event()
        started = []

        async def fake(name, result):
            started.append(name)
            await gate.wait()
            return result

        async def tri(_):
            return await fake("triage_agt", AgentResult(
                agent="triage_agt", intervention=InterventionType.RETRY_AT,
                risk_score=0.4, reasoning="Transient decline."))

        async def sch(_):
            return await fake("schedule_agt", AgentResult(
                agent="schedule_agt", proposed_retry_ts=datetime(2026, 9, 5, 10, 0),
                liquidity_window_ok=True))

        async def cpy(_):
            return await fake("copy_agt", AgentResult(
                agent="copy_agt", reasoning="Empathetic retry recommendation.",
                customer_copy="We will help keep your service active."))

        old = orch.triage_agt, orch.schedule_agt, orch.copy_agt
        orch.triage_agt, orch.schedule_agt, orch.copy_agt = tri, sch, cpy
        try:
            task = asyncio.create_task(orch.propose_intervention_parallel(_state()))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert set(started) == {"triage_agt", "schedule_agt", "copy_agt"}
            gate.set()
            prop, agents = await task
        finally:
            orch.triage_agt, orch.schedule_agt, orch.copy_agt = old

        assert prop.intervention == InterventionType.RETRY_AT
        assert prop.proposed_retry_ts == datetime(2026, 9, 5, 10, 0)
        assert prop.customer_copy == "We will help keep your service active."
        assert set(agents) == {"triage_agt", "schedule_agt", "copy_agt"}

    asyncio.run(run())


def test_merged_proposal_still_enters_guardrails():
    state = _state().model_copy(update={"retry_count": 3, "max_retry_budget": 3})
    prop = LLMProposal(
        intervention=InterventionType.RETRY_AT,
        proposed_retry_ts=datetime(2026, 9, 6, 10, 0),
        reasoning="Retry proposed by triage and schedule agents.",
    )
    verdict = evaluate(state, prop)
    assert verdict.final_intervention == InterventionType.ESCALATE_TO_HUMAN
    assert verdict.was_overridden is True


def test_injected_astra_client_routes_all_agents_and_logs_named_events():
    class FakeAstra:
        def __init__(self):
            self.calls = []

        def propose_agent_result(self, *, agent, state):
            self.calls.append(agent)
            return {
                "agent": agent,
                "intervention": "RETRY_AT" if agent == "triage_agt" else None,
                "proposed_retry_ts": "2026-09-05T10:00:00" if agent == "schedule_agt" else None,
                "reasoning": f"{agent} model result",
                "customer_copy": "We can help keep your account active." if agent == "copy_agt" else None,
            }

    client = FakeAstra()
    orch.set_model_client(client)
    try:
        proposal, agents = asyncio.run(orch.propose_intervention_parallel(_state()))
    finally:
        orch.set_model_client(None)

    assert set(client.calls) == {"triage_agt", "schedule_agt", "copy_agt"}
    assert proposal.intervention == InterventionType.RETRY_AT
    assert set(agents) == set(client.calls)

    store = StateStore(":memory:")
    for result in agents.values():
        store.log_agent_done("inv_async_001", result)
    assert [e.event_type for e in reversed(store.get_events())] == [
        "TRIAGE_AGENT_DONE", "SCHEDULE_AGENT_DONE", "COPY_AGENT_DONE"
    ]


def test_copy_agent_uses_customer_history_context():
    state = _state().model_copy(update={"customer_id": "cust_context_007"})
    seed_history([{
        "customer_id": "cust_context_007",
        "text": "Customer previously requested shifting the billing date to the 10th after salary credit.",
    }])
    try:
        res = asyncio.run(orch.copy_agt(state))
    finally:
        seed_history([])

    assert res.historical_context
    assert "billing date" in res.customer_copy.lower()
    assert "10th" in res.customer_copy
