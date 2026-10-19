import asyncio
from datetime import datetime

from app import llm_orchestrator as orch
from app.guardrail_engine import evaluate
from app.schemas import AgentResult, InterventionType, LLMProposal, RecoveryState


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
